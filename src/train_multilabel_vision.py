"""Train a multi-label ECG vision classifier.

Input manifest must include `image_path`, `split`, and one column per label.
Labels default to the PTB-XL superclasses (NORM, MI, STTC, CD, HYP); pass
--labels for a dataset with its own taxonomy, e.g. the ACS database's
OMI/STEMI/NSTEMI/UA/AMI.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
import timm
import torch
from PIL import Image
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms


# Default label set (PTB-XL diagnostic superclasses). Datasets with their own
# taxonomy - the ACS database ships OMI/STEMI/NSTEMI/UA/AMI - pass --labels.
LABELS = ["NORM", "MI", "STTC", "CD", "HYP"]
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
HALF_MEAN = (0.5, 0.5, 0.5)
HALF_STD = (0.5, 0.5, 0.5)


class ECGImageDataset(Dataset):
    def __init__(self, frame: pd.DataFrame, transform, labels: list[str] = LABELS) -> None:
        self.frame = frame.reset_index(drop=True)
        self.transform = transform
        self.labels = labels

    def __len__(self) -> int:
        return len(self.frame)

    def __getitem__(self, index: int):
        row = self.frame.iloc[index]
        image = Image.open(row["image_path"]).convert("RGB")
        labels = torch.tensor(row[self.labels].astype(float).to_numpy(), dtype=torch.float32)
        return self.transform(image), labels


def build_model(name: str, num_labels: int, pretrained: bool) -> nn.Module:
    if name == "resnet18":
        weights = models.ResNet18_Weights.DEFAULT if pretrained else None
        model = models.resnet18(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, num_labels)
        return model
    if name == "resnet50":
        weights = models.ResNet50_Weights.DEFAULT if pretrained else None
        model = models.resnet50(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, num_labels)
        return model
    if name == "convnext_tiny":
        weights = models.ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None
        model = models.convnext_tiny(weights=weights)
        model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, num_labels)
        return model
    if name in ("convnextv2_base", "convnextv2_base.fcmae_ft_in22k_in1k", "convnextv2_base.fcmae_ft_in1k"):
        return timm.create_model("convnextv2_base", pretrained=pretrained, num_classes=num_labels, in_chans=3)
    raise ValueError(f"Unsupported model: {name}")


def load_inference_checkpoint(checkpoint_path, device: torch.device) -> dict:
    """Load either checkpoint format produced in this project:

    - legacy (this repo's train_multilabel_vision.py): keys `model`, `labels`, `args`,
      ImageNet-pretrained normalization, 224x224 eval crop.
    - ConvNeXtV2 (Kaggle research notebook): keys `model_state_dict`, `label_names`,
      `config`, `threshold`, normalization mean/std=0.5, 224x224 resize.
    """
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)

    if "model_state_dict" in checkpoint:
        labels = checkpoint["label_names"]
        config = checkpoint["config"]
        model_name = config["model_name"]
        model = build_model(model_name, len(labels), pretrained=False).to(device)
        model.load_state_dict(checkpoint["model_state_dict"])
        thresholds = {label: float(t) for label, t in checkpoint.get("threshold", {}).items()}
        image_size = int(config.get("image_size", 224))
        mean, std = HALF_MEAN, HALF_STD
    else:
        labels = checkpoint["labels"]
        model_name = checkpoint["args"]["model"]
        model = build_model(model_name, len(labels), pretrained=False).to(device)
        model.load_state_dict(checkpoint["model"])
        thresholds = {label: 0.5 for label in labels}
        image_size = 224
        mean, std = IMAGENET_MEAN, IMAGENET_STD

    model.eval()
    return {
        "model": model,
        "labels": labels,
        "model_name": model_name,
        "image_size": image_size,
        "mean": mean,
        "std": std,
        "thresholds": thresholds,
    }


def compute_sample_weights(frame: pd.DataFrame, labels: list[str] = LABELS) -> np.ndarray:
    """Per-sample weight for WeightedRandomSampler: each sample takes the
    inverse-frequency weight of its rarest positive label, so images with a
    minority finding (e.g. MI, CD, HYP) get oversampled relative to NORM."""
    label_matrix = frame[labels].to_numpy(dtype=np.float32)
    freq = label_matrix.mean(axis=0)
    inv_freq = 1.0 / np.maximum(freq, 1e-6)
    per_sample = np.where(label_matrix > 0, inv_freq, 0.0)
    weights = per_sample.max(axis=1)
    weights[weights == 0] = inv_freq.min()
    return weights


def set_seed(seed: int) -> None:
    """Seed Python, NumPy and Torch so a run can be repeated.

    On the small splits used here the run-to-run spread has been wider than the
    gap between configurations (macro F1 0.416 then 0.457 from the same
    command), so a fixed seed is the minimum needed to tell a real improvement
    from noise. Average several seeds before reporting a number.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def make_transforms(image_size: int = 224, mean=IMAGENET_MEAN, std=IMAGENET_STD):
    train_tfms = transforms.Compose(
        [
            transforms.Resize((image_size + 32, image_size + 32)),
            transforms.RandomResizedCrop(image_size, scale=(0.90, 1.0)),
            transforms.ToTensor(),
            transforms.Normalize(mean=mean, std=std),
        ]
    )
    eval_tfms = transforms.Compose(
        [
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=mean, std=std),
        ]
    )
    return train_tfms, eval_tfms


@torch.no_grad()
def predict_probabilities(model: nn.Module, loader: DataLoader, device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    """Targets and sigmoid probabilities in loader order (val/test use
    shuffle=False, so rows line up with the split frame and its ecg_id)."""
    model.eval()
    targets, probabilities = [], []
    for images, target in loader:
        probabilities.append(torch.sigmoid(model(images.to(device))).cpu().numpy())
        targets.append(target.numpy())
    return np.concatenate(targets), np.concatenate(probabilities)


@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, device: torch.device, labels: list[str] = LABELS) -> dict[str, float]:
    model.eval()
    all_targets = []
    all_probs = []
    for images, targets in loader:
        logits = model(images.to(device))
        probs = torch.sigmoid(logits).cpu().numpy()
        all_probs.append(probs)
        all_targets.append(targets.numpy())

    y_true = np.concatenate(all_targets, axis=0)
    y_prob = np.concatenate(all_probs, axis=0)
    y_pred = (y_prob >= 0.5).astype(int)

    # A label with no positive (or no negative) in this split has no defined
    # AUROC/AUPRC. Averaging over all five would then poison the macro score
    # for the whole run - Chapman's test split holds zero MI records, which
    # is enough to turn macro AUROC into NaN. Score the usable labels only
    # and record how many were scored.
    scorable = [idx for idx in range(len(labels)) if 0 < y_true[:, idx].sum() < len(y_true)]

    metrics: dict[str, float] = {}
    metrics["micro_f1"] = float(f1_score(y_true, y_pred, average="micro", zero_division=0))
    metrics["micro_auprc"] = float(average_precision_score(y_true, y_prob, average="micro"))
    metrics["scored_labels"] = float(len(scorable))

    if scorable:
        # macro_f1 has to skip the unscorable labels for the same reason as the
        # rest: a label with no positive in the split scores F1 = 0 even when
        # the model correctly predicts it negative everywhere, and averaging
        # that 0 in as a real value costs 1/len(labels) of macro F1 - Chapman's
        # empty MI column alone cost about 0.1 absolute. micro_f1 keeps every
        # label because it pools counts, so a correctly all-negative label adds
        # nothing rather than a zero.
        metrics["macro_f1"] = float(f1_score(y_true[:, scorable], y_pred[:, scorable], average="macro", zero_division=0))
        metrics["macro_auprc"] = float(average_precision_score(y_true[:, scorable], y_prob[:, scorable], average="macro"))
        metrics["macro_auroc"] = float(roc_auc_score(y_true[:, scorable], y_prob[:, scorable], average="macro"))
        metrics["micro_auroc"] = float(roc_auc_score(y_true[:, scorable], y_prob[:, scorable], average="micro"))
    else:
        metrics["macro_f1"] = metrics["macro_auprc"] = metrics["macro_auroc"] = metrics["micro_auroc"] = float("nan")

    for idx, label in enumerate(labels):
        if idx in scorable:
            metrics[f"{label}_auroc"] = float(roc_auc_score(y_true[:, idx], y_prob[:, idx]))
            metrics[f"{label}_auprc"] = float(average_precision_score(y_true[:, idx], y_prob[:, idx]))
        else:
            metrics[f"{label}_auroc"] = float("nan")
            metrics[f"{label}_auprc"] = float("nan")
        metrics[f"{label}_positives"] = float(y_true[:, idx].sum())
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", nargs="+", default=LABELS, help="Label columns to train on (default: PTB-XL superclasses).")
    parser.add_argument("--model", choices=["resnet18", "resnet50", "convnext_tiny", "convnextv2_base"], default="resnet18")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--no-pretrained", action="store_true")
    parser.add_argument("--no-weighted-sampler", action="store_true", help="Disable WeightedRandomSampler; use plain shuffling instead.")
    parser.add_argument("--seed", type=int, default=42, help="Seed for Python/NumPy/Torch RNGs; vary it to measure run-to-run spread.")
    parser.add_argument("--output", type=Path, default=Path("models/vinstroke_ecg.pt"))
    parser.add_argument("--metrics-json", type=Path, default=None, help="Write test metrics (overall and per dataset) to this path.")
    parser.add_argument("--probabilities", type=Path, default=None, help="Save val/test probabilities (npz) for ensembling and clinical read-out.")
    args = parser.parse_args()
    set_seed(args.seed)

    frame = pd.read_csv(args.manifest)
    labels = list(args.labels)
    missing = [label for label in labels if label not in frame.columns]
    if missing:
        raise SystemExit(f"Manifest thieu cot nhan: {missing}")
    train_frame = frame[frame["split"] == "train"].copy()
    val_frame = frame[frame["split"] == "val"].copy()
    test_frame = frame[frame["split"] == "test"].copy()

    train_tfms, eval_tfms = make_transforms()
    if args.no_weighted_sampler:
        train_loader = DataLoader(ECGImageDataset(train_frame, train_tfms, labels), batch_size=args.batch_size, shuffle=True, num_workers=2)
    else:
        sample_weights = compute_sample_weights(train_frame, labels)
        sampler = WeightedRandomSampler(torch.as_tensor(sample_weights, dtype=torch.double), num_samples=len(sample_weights), replacement=True)
        train_loader = DataLoader(ECGImageDataset(train_frame, train_tfms, labels), batch_size=args.batch_size, sampler=sampler, num_workers=2)
    val_loader = DataLoader(ECGImageDataset(val_frame, eval_tfms, labels), batch_size=args.batch_size, shuffle=False, num_workers=2)
    test_loader = DataLoader(ECGImageDataset(test_frame, eval_tfms, labels), batch_size=args.batch_size, shuffle=False, num_workers=2)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(args.model, len(labels), pretrained=not args.no_pretrained).to(device)

    counts = train_frame[labels].sum(axis=0).to_numpy(dtype=np.float32)
    negatives = len(train_frame) - counts
    pos_weight = torch.tensor(negatives / np.maximum(counts, 1), dtype=torch.float32, device=device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    best_macro_auprc = -1.0

    print(f"Device: {device}")
    print(f"Train/val/test: {len(train_frame)}/{len(val_frame)}/{len(test_frame)}")
    print(f"Train label counts: {dict(zip(labels, counts.astype(int).tolist()))}")

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        for images, targets in train_loader:
            images = images.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * images.size(0)

        val_metrics = evaluate(model, val_loader, device, labels)
        loss_value = running_loss / max(len(train_loader.dataset), 1)
        print(f"Epoch {epoch}: loss={loss_value:.4f} val={val_metrics}")

        if val_metrics["macro_auprc"] > best_macro_auprc:
            best_macro_auprc = val_metrics["macro_auprc"]
            torch.save({"model": model.state_dict(), "labels": labels, "args": vars(args)}, args.output)
            print(f"Saved best checkpoint: {args.output}")

    checkpoint = torch.load(args.output, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model"])
    test_metrics = evaluate(model, test_loader, device, labels)
    print(f"Test: {test_metrics}")

    if args.probabilities:
        y_val, prob_val = predict_probabilities(model, val_loader, device)
        y_test, prob_test = predict_probabilities(model, test_loader, device)
        args.probabilities.parent.mkdir(parents=True, exist_ok=True)
        # ecg_id lets an ensemble align members by record: this track and the
        # 1D signal cache cover different subsets of PTB-XL, so matching on row
        # position would silently pair up different patients.
        np.savez(
            args.probabilities,
            labels=np.array(labels),
            y_val=y_val, prob_val=prob_val,
            y_test=y_test, prob_test=prob_test,
            ecg_id_val=val_frame["ecg_id"].to_numpy() if "ecg_id" in val_frame.columns else np.arange(len(y_val)),
            ecg_id_test=test_frame["ecg_id"].to_numpy() if "ecg_id" in test_frame.columns else np.arange(len(y_test)),
        )
        print(f"Da ghi xac suat: {args.probabilities}")

    results = {"overall": test_metrics}
    run_record = {
        "run": args.output.stem,
        "manifest": str(args.manifest),
        "config": {key: str(value) for key, value in vars(args).items()},
        "counts": {
            "train": len(train_frame),
            "val": len(val_frame),
            "test": len(test_frame),
            "train_positives": {label: int(count) for label, count in zip(labels, counts.astype(int).tolist())},
        },
        "results": results,
    }
    # A merged manifest (scripts/merge_manifests.py) carries a `dataset`
    # column: score each source database separately so a combined run shows
    # where the model actually generalises.
    if "dataset" in test_frame.columns and test_frame["dataset"].nunique() > 1:
        for name, group in test_frame.groupby("dataset"):
            group_loader = DataLoader(ECGImageDataset(group, eval_tfms, labels), batch_size=args.batch_size, shuffle=False, num_workers=2)
            group_metrics = evaluate(model, group_loader, device, labels)
            results[name] = group_metrics
            print(f"Test[{name}] n={len(group)}: {group_metrics}")

    if args.metrics_json:
        args.metrics_json.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_json.write_text(json.dumps(run_record, indent=2))
        print(f"Saved metrics: {args.metrics_json}")


if __name__ == "__main__":
    main()

