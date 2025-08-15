#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import os
import json
import argparse
import numpy as np
import pandas as pd
import yaml

from sklearn.preprocessing import OneHotEncoder, StandardScaler, MinMaxScaler, LabelEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer

# ----------------------------
# Defaults
# ----------------------------
DEFAULT_CFG = {
    "input_path": "dataset/diabetes_dataset00.csv",
    "output_dir": "dataset/clean",
    "target": "",
    "drop_age_zero": True,
    "iqr": {"enabled": True, "k": 1.5},
    "encode": {"label_encode_target": False},  # app.py usually sets this True via env
    "scaling": "standard",                     # standard | minmax | none
    "save": {"csv": False, "xlsx": True, "report_json": True}  # app.py sets csv=True
}

# ----------------------------
# Helpers
# ----------------------------
def read_any(p: Path) -> pd.DataFrame:
    return pd.read_excel(p) if p.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(p)

def iqr_filter(df: pd.DataFrame, numeric_cols, k: float):
    if not numeric_cols or k is None:
        return df, {"rows_flagged_any_numeric": 0, "rows_kept": len(df),
                    "rows_before": len(df), "per_column_bounds": {}}
    mask = pd.Series(False, index=df.index)
    percol = {}
    for col in numeric_cols:
        s = pd.to_numeric(df[col], errors="coerce")
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = (q3 - q1)
        if pd.isna(iqr) or iqr == 0:
            percol[col] = {"lower": None, "upper": None, "removed": 0}
            continue
        lower, upper = q1 - k * iqr, q3 + k * iqr
        out = (s < lower) | (s > upper)
        mask |= out.fillna(False)
        percol[col] = {
            "lower": float(lower),
            "upper": float(upper),
            "removed": int(out.sum(skipna=True)),
        }
    before = len(df)
    kept = df.loc[~mask].copy()
    return kept, {
        "rows_flagged_any_numeric": int(mask.sum()),
        "rows_kept": len(kept),
        "rows_before": before,
        "per_column_bounds": percol,
    }

def load_config(cfg_path: str):
    # Allow passing a directory that contains config.yaml
    if os.path.isdir(cfg_path):
        candidate = os.path.join(cfg_path, "config.yaml")
        if os.path.isfile(candidate):
            cfg_path = candidate

    if not os.path.isfile(cfg_path):
        print(f"[WARN] Config file not found at '{cfg_path}'. Using defaults.")
        return DEFAULT_CFG.copy()

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # Deep merge defaults and provided config
    def _deep_merge(base, override):
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = _deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    merged = _deep_merge(DEFAULT_CFG.copy(), cfg)
    print(f"[INFO] Using config file: {cfg_path}")
    return merged

def apply_overrides(cfg, args):
    if args.input_path:  cfg["input_path"] = args.input_path
    if args.output_dir:  cfg["output_dir"] = args.output_dir
    if args.target is not None: cfg["target"] = args.target or ""
    if args.scaling:     cfg["scaling"] = args.scaling
    if args.drop_age_zero is not None: cfg["drop_age_zero"] = args.drop_age_zero
    if args.iqr_enabled is not None:   cfg.setdefault("iqr", {})["enabled"] = args.iqr_enabled
    if args.iqr_k is not None:         cfg.setdefault("iqr", {})["k"] = args.iqr_k
    if args.label_encode_target is not None:
        cfg.setdefault("encode", {})["label_encode_target"] = args.label_encode_target
    # new save flags
    if hasattr(args, "save_csv") and args.save_csv is not None:
        cfg.setdefault("save", {})["csv"] = args.save_csv
    if hasattr(args, "save_xlsx") and args.save_xlsx is not None:
        cfg.setdefault("save", {})["xlsx"] = args.save_xlsx
    if hasattr(args, "save_report") and args.save_report is not None:
        cfg.setdefault("save", {})["report_json"] = args.save_report
    return cfg

def env_override(cfg):
    cfg["input_path"] = os.getenv("PREP_INPUT_PATH", cfg.get("input_path"))
    cfg["output_dir"] = os.getenv("PREP_OUTPUT_DIR", cfg.get("output_dir"))
    if os.getenv("PREP_TARGET") is not None:
        cfg["target"] = os.getenv("PREP_TARGET")
    if os.getenv("PREP_SCALING") in {"standard", "minmax", "none"}:
        cfg["scaling"] = os.getenv("PREP_SCALING")
    if os.getenv("PREP_DROP_AGE_ZERO"):
        cfg["drop_age_zero"] = os.getenv("PREP_DROP_AGE_ZERO").lower() == "true"
    if os.getenv("PREP_IQR_ENABLED"):
        cfg.setdefault("iqr", {})["enabled"] = os.getenv("PREP_IQR_ENABLED").lower() == "true"
    if os.getenv("PREP_IQR_K"):
        cfg.setdefault("iqr", {})["k"] = float(os.getenv("PREP_IQR_K"))
    if os.getenv("PREP_LABEL_ENCODE_TARGET"):
        cfg.setdefault("encode", {})["label_encode_target"] = os.getenv("PREP_LABEL_ENCODE_TARGET").lower() == "true"
    # new save flags
    if os.getenv("PREP_SAVE_CSV") is not None:
        cfg.setdefault("save", {})["csv"] = os.getenv("PREP_SAVE_CSV").lower() == "true"
    if os.getenv("PREP_SAVE_XLSX") is not None:
        cfg.setdefault("save", {})["xlsx"] = os.getenv("PREP_SAVE_XLSX").lower() == "true"
    if os.getenv("PREP_SAVE_REPORT") is not None:
        cfg.setdefault("save", {})["report_json"] = os.getenv("PREP_SAVE_REPORT").lower() == "true"
    return cfg

def build_categorical_tf():
    # sklearn < 1.2 uses 'sparse', >=1.2 adds 'sparse_output'
    try:
        return Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("ohe", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ])
    except TypeError:
        return Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("ohe", OneHotEncoder(handle_unknown="ignore", sparse=False)),
        ])

# ----------------------------
# Core processing (Programmatic API)
# ----------------------------
def run_preprocessing(cfg: dict):
    in_path = Path(cfg["input_path"])
    outdir = Path(cfg["output_dir"])
    outdir.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise FileNotFoundError(f"[ERROR] Input file not found: {in_path}")

    target = (cfg.get("target") or "").strip() or None
    scaling = cfg.get("scaling", "standard")
    drop_age_zero = bool(cfg.get("drop_age_zero", True))
    iqr_enabled = cfg.get("iqr", {}).get("enabled", True)
    iqr_k = float(cfg.get("iqr", {}).get("k", 1.5))
    label_encode_target = bool(cfg.get("encode", {}).get("label_encode_target", False))
    save_csv = bool(cfg.get("save", {}).get("csv", False))
    save_xlsx = bool(cfg.get("save", {}).get("xlsx", True))
    save_report = bool(cfg.get("save", {}).get("report_json", True))

    # 1) Load
    df = read_any(in_path)
    rows_before = len(df)
    print(f"[INFO] Loaded {in_path} with {rows_before} rows and {df.shape[1]} columns.")

    # 2) Drop Age==0
    age_removed = 0
    if drop_age_zero and "Age" in df.columns:
        age_removed = int((df["Age"] == 0).sum())
        if age_removed:
            df = df.loc[df["Age"] != 0].copy()
        print(f"[INFO] Dropped Age==0 rows: {age_removed}")

    # 3) Outlier removal (IQR)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if iqr_enabled:
        df, iqr_report = iqr_filter(df, numeric_cols, k=iqr_k)
        print(f"[INFO] IQR filter k={iqr_k}; rows removed across any numeric: {iqr_report['rows_flagged_any_numeric']}")
    else:
        iqr_report = {"rows_flagged_any_numeric": 0, "rows_kept": len(df),
                      "rows_before": len(df), "per_column_bounds": {}}
        print("[INFO] IQR filter disabled.")

    # 4) Split X/y (auto-detect target if not provided)
    if target is None:
        for c in ["Target", "Outcome", "label", "Label", "diabetes", "Class", "class"]:
            if c in df.columns:
                target = c
                break
    if target and target in df.columns:
        y = df[target].copy()
        X = df.drop(columns=[target]).copy()
        print(f"[INFO] Using target column: {target}")
    else:
        y, X = None, df.copy()
        target = None
        print("[INFO] No target column used (features-only processing).")

    # Try to coerce numeric-like target; otherwise leave for label encoding
    if y is not None and y.dtype == object:
        try:
            y = pd.to_numeric(y)
        except Exception:
            pass

    # 5) Build preprocessors
    num_features = X.select_dtypes(include=[np.number]).columns.tolist()
    # Preserve original column order for categoricals (not set-difference)
    cat_features = [c for c in X.columns if c not in num_features]
    print(f"[INFO] Numeric features: {len(num_features)} | Categorical features: {len(cat_features)}")

    scaler = {"standard": StandardScaler(),
              "minmax": MinMaxScaler(),
              "none": "passthrough"}[scaling]

    numeric_tf = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", scaler)
    ])
    categorical_tf = build_categorical_tf()

    pre = ColumnTransformer([
        ("num", numeric_tf, num_features),
        ("cat", categorical_tf, cat_features),
    ])

    Xt = pre.fit_transform(X)

    # 6) Feature names
    num_names = list(num_features)
    if cat_features:
        try:
            ohe = pre.named_transformers_["cat"].named_steps["ohe"]
            cat_names = list(ohe.get_feature_names_out(cat_features))
        except Exception:
            # Fallback if OHE doesn't expose names
            cat_names = [f"{c}_ohe" for c in cat_features]
    else:
        cat_names = []
    feature_names = num_names + cat_names
    X_clean = pd.DataFrame(Xt, columns=feature_names, index=X.index)

    # Target encoding (recommended if labels are strings)
    target_info = {"encoded": False, "classes": None}
    if y is not None:
        if label_encode_target and (not np.issubdtype(pd.Series(y).dtype, np.number)):
            le = LabelEncoder()
            y_clean = pd.Series(le.fit_transform(y), name=target, index=y.index)
            target_info = {"encoded": True, "classes": list(map(str, le.classes_))}
        else:
            y_clean = y.copy()
    else:
        y_clean = None

    # 7) Save artifacts (CSV / XLSX / JSON report)
    base = in_path.stem + "_clean"
    outputs = {}

    feat_path = outdir / f"{base}_feature_names.json"
    with open(feat_path, "w", encoding="utf-8") as f:
        json.dump(feature_names, f, indent=2)
    outputs["feature_names_json"] = str(feat_path)

    if save_csv:
        X_csv = outdir / f"{base}_X.csv"
        X_clean.to_csv(X_csv, index=False)
        outputs["X_csv"] = str(X_csv)
        if y_clean is not None:
            y_csv = outdir / f"{base}_y.csv"
            pd.DataFrame({target: y_clean}).to_csv(y_csv, index=False)
            outputs["y_csv"] = str(y_csv)

    if save_xlsx:
        try:
            X_xlsx = outdir / f"{base}_X.xlsx"
            X_clean.to_excel(X_xlsx, index=False)
            outputs["X_xlsx"] = str(X_xlsx)
            if y_clean is not None:
                y_xlsx = outdir / f"{base}_y.xlsx"
                pd.DataFrame({target: y_clean}).to_excel(y_xlsx, index=False)
                outputs["y_xlsx"] = str(y_xlsx)
        except Exception as e:
            print(f"[WARN] Excel save failed ({e}). Install 'openpyxl' to enable XLSX output.")

    report = {
        "rows_before": rows_before,
        "rows_after": int(len(df)),
        "age_zero_rows_removed": age_removed,
        "iqr_report": iqr_report,
        "target_column": target,
        "num_features_before_ohe": num_features,
        "cat_features_before_ohe": cat_features,
        "feature_count_after_ohe": int(X_clean.shape[1]),
        "scaling": scaling,
        "label_encoder_on_target": target_info,
        "outputs": outputs,
    }

    if save_report:
        rep_path = outdir / f"{base}_report.json"
        with open(rep_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"[INFO] Report written to: {rep_path}")

    return report

def preprocess(input_path: str,
               output_dir: str,
               target: str | None = None,
               scaling: str = "standard",
               drop_age_zero: bool = True,
               iqr_enabled: bool = True,
               iqr_k: float = 1.5,
               label_encode_target: bool = True,
               save_csv: bool = True,
               save_xlsx: bool = False,
               save_report: bool = True):
    """
    Programmatic entry point used by model_training/app.py
    Returns a dict report with output paths and metadata.
    """
    cfg = DEFAULT_CFG.copy()
    cfg["input_path"] = input_path
    cfg["output_dir"] = output_dir
    cfg["target"] = target or ""
    cfg["scaling"] = scaling
    cfg["drop_age_zero"] = drop_age_zero
    cfg.setdefault("iqr", {})["enabled"] = iqr_enabled
    cfg.setdefault("iqr", {})["k"] = iqr_k
    cfg.setdefault("encode", {})["label_encode_target"] = label_encode_target
    cfg.setdefault("save", {})["csv"] = save_csv
    cfg.setdefault("save", {})["xlsx"] = save_xlsx
    cfg.setdefault("save", {})["report_json"] = save_report
    return run_preprocessing(cfg)

# ----------------------------
# CLI
# ----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/config.yaml")
    parser.add_argument("--input_path")
    parser.add_argument("--output_dir")
    parser.add_argument("--target")
    parser.add_argument("--scaling", choices=["standard", "minmax", "none"])
    parser.add_argument("--drop_age_zero", type=lambda x: x.lower() == "true")
    parser.add_argument("--iqr_enabled", type=lambda x: x.lower() == "true")
    parser.add_argument("--iqr_k", type=float)
    parser.add_argument("--label_encode_target", type=lambda x: x.lower() == "true")
    # new save flags
    parser.add_argument("--save_csv", type=lambda x: x.lower() == "true")
    parser.add_argument("--save_xlsx", type=lambda x: x.lower() == "true")
    parser.add_argument("--save_report", type=lambda x: x.lower() == "true")

    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg = apply_overrides(cfg, args)
    cfg = env_override(cfg)

    report = run_preprocessing(cfg)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()