# import os
# import io
# import json
# from pathlib import Path

# import numpy as np
# import pandas as pd
# from flask import Flask, request, jsonify
# from sklearn.model_selection import train_test_split
# from sklearn.metrics import accuracy_score, f1_score, classification_report
# from xgboost import XGBClassifier
# import joblib

# # --- import sibling preprocessing module ---
# import sys
# ROOT = Path(__file__).resolve().parents[1]  # project root (../307-Project)
# # make the package "data_preprocessing" importable
# if str(ROOT) not in sys.path:
#     sys.path.append(str(ROOT))
# from data_preprocessing import preprocessing as prep  # exposes preprocess()

# app = Flask(__name__)

# # ---- Paths/Configs ----
# DEFAULT_DATASET_PATH = "shared/data/diabetes_dataset00.csv"
# LATEST_DATASET_PATH = "/mnt/data/latest_dataset.csv"
# MODEL_DIR = os.getenv("MODEL_DIR", "/mnt/data/models")
# PREP_OUTPUT_DIR = os.getenv("PREP_OUTPUT_DIR", "/mnt/data/clean")


# def train_model_from_path(dataset_path: str):
#     # Ensure dirs
#     os.makedirs(PREP_OUTPUT_DIR, exist_ok=True)
#     os.makedirs(MODEL_DIR, exist_ok=True)

#     # Run preprocessing (ALL preprocessing is delegated here)
#     try:
#         report = prep.preprocess(
#             input_path=dataset_path,
#             output_dir=PREP_OUTPUT_DIR,
#             target=os.getenv("PREP_TARGET", "Target") or None,
#             scaling=os.getenv("PREP_SCALING", "standard"),
#             drop_age_zero=os.getenv("PREP_DROP_AGE_ZERO", "true").lower() == "true",
#             iqr_enabled=os.getenv("PREP_IQR_ENABLED", "true").lower() == "true",
#             iqr_k=float(os.getenv("PREP_IQR_K", "1.5")),
#             label_encode_target=os.getenv("PREP_LABEL_ENCODE_TARGET", "true").lower() == "true",
#             save_csv=True,
#             save_xlsx=False,
#             save_report=True,
#         )
#     except Exception as e:
#         raise RuntimeError(f"Preprocessing failed: {e}")

#     outputs = report.get("outputs", {})
#     X_path = outputs.get("X_csv") or outputs.get("X_xlsx")
#     y_path = outputs.get("y_csv") or outputs.get("y_xlsx")

#     if not X_path or not os.path.exists(X_path):
#         raise FileNotFoundError("Preprocessing did not produce X features file.")
#     if not y_path or not os.path.exists(y_path):
#         raise FileNotFoundError("Preprocessing did not produce y labels file.")

#     # Load clean features
#     if X_path.endswith(".xlsx"):
#         X_df = pd.read_excel(X_path)
#         y_df = pd.read_excel(y_path)
#     else:
#         X_df = pd.read_csv(X_path)
#         y_df = pd.read_csv(y_path)

#     X = X_df.values
#     # y_df has one column named after the target; flatten to 1D
#     y = np.ravel(y_df.values)

#     # Split 70/15/15
#     X_temp, X_test, y_temp, y_test = train_test_split(
#         X, y, test_size=0.15, random_state=42, stratify=y
#     )
#     X_train, X_val, y_train, y_val = train_test_split(
#         X_temp, y_temp, test_size=0.1765, random_state=42, stratify=y_temp
#     )

#     # Choose objective
#     num_classes = len(np.unique(y))
#     objective = "multi:softprob" if num_classes > 2 else "binary:logistic"

#     # Train
#     xgb = XGBClassifier(
#         n_estimators=200,
#         learning_rate=0.1,
#         max_depth=8,
#         objective=objective,
#         eval_metric="mlogloss",
#         use_label_encoder=False,
#         n_jobs=-1,
#         random_state=42,
#     )
#     xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=True)

#     # Evaluate
#     y_val_pred = xgb.predict(X_val)
#     y_test_pred = xgb.predict(X_test)

#     metrics = {
#         "val": {
#             "accuracy": float(accuracy_score(y_val, y_val_pred)),
#             "f1_macro": float(f1_score(y_val, y_val_pred, average="macro")),
#             "report": classification_report(y_val, y_val_pred, output_dict=True),
#         },
#         "test": {
#             "accuracy": float(accuracy_score(y_test, y_test_pred)),
#             "f1_macro": float(f1_score(y_test, y_test_pred, average="macro")),
#             "report": classification_report(y_test, y_test_pred, output_dict=True),
#         },
#     }

#     # Persist artifacts
#     model_path = os.path.join(MODEL_DIR, "trained_model.pkl")
#     joblib.dump(xgb, model_path)

#     classes_info = report.get("label_encoder_on_target", {})
#     classes_path = os.path.join(MODEL_DIR, "target_labels.json")
#     with open(classes_path, "w", encoding="utf-8") as f:
#         json.dump(classes_info, f, indent=2)

#     prep_report_path = os.path.join(MODEL_DIR, "preprocessing_report.json")
#     with open(prep_report_path, "w", encoding="utf-8") as f:
#         json.dump(report, f, indent=2)

#     metrics_path = os.path.join(MODEL_DIR, "metrics.json")
#     with open(metrics_path, "w", encoding="utf-8") as f:
#         json.dump(metrics, f, indent=2)

#     return {
#         "rows_train": int(X_train.shape[0]),
#         "rows_val": int(X_val.shape[0]),
#         "rows_test": int(X_test.shape[0]),
#         "features": int(X_train.shape[1]),
#         "artifacts": {
#             "model_path": model_path,
#             "classes_path": classes_path,
#             "metrics_path": metrics_path,
#             "preprocessing_report_path": prep_report_path,
#             "X_path": X_path,
#             "y_path": y_path,
#         },
#         "metrics_summary": {
#             "val_accuracy": metrics["val"]["accuracy"],
#             "val_f1_macro": metrics["val"]["f1_macro"],
#             "test_accuracy": metrics["test"]["accuracy"],
#             "test_f1_macro": metrics["test"]["f1_macro"],
#         },
#     }


# # --- Health check ---
# @app.route("/healthz", methods=["GET"])
# def healthz():
#     model_exists = os.path.exists(os.path.join(MODEL_DIR, "trained_model.pkl"))
#     return jsonify({"status": "ok", "modelReady": model_exists}), 200


# # --- Metrics endpoint ---
# @app.route("/metrics", methods=["GET"])
# def get_metrics():
#     metrics_path = os.path.join(MODEL_DIR, "metrics.json")
#     if not os.path.exists(metrics_path):
#         return jsonify({"error": "metrics not found"}), 404
#     with open(metrics_path, "r", encoding="utf-8") as f:
#         data = json.load(f)
#     return jsonify(data), 200


# # --- Training routes ---
# @app.route("/train", methods=["POST"])
# def train():
#     try:
#         if "file" in request.files:
#             raw = request.files["file"].read()
#             os.makedirs(os.path.dirname(LATEST_DATASET_PATH), exist_ok=True)
#             with open(LATEST_DATASET_PATH, "wb") as f:
#                 f.write(raw)
#             dataset_path = LATEST_DATASET_PATH
#         elif os.path.exists(LATEST_DATASET_PATH):
#             dataset_path = LATEST_DATASET_PATH
#         else:
#             dataset_path = DEFAULT_DATASET_PATH

#         info = train_model_from_path(dataset_path)
#         return jsonify({"status": "Training complete", "info": info}), 200
#     except Exception as e:
#         return jsonify({"error": str(e)}), 400


# @app.route("/train", methods=["GET"])
# def retrain():
#     try:
#         dataset_path = (
#             LATEST_DATASET_PATH if os.path.exists(LATEST_DATASET_PATH) else DEFAULT_DATASET_PATH
#         )
#         info = train_model_from_path(dataset_path)
#         return jsonify({"status": "Retrained using last used/default dataset", "info": info}), 200
#     except Exception as e:
#         return jsonify({"error": str(e)}), 400


# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=8000)

import os
import io
import json
import argparse
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, request, jsonify
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, classification_report
from xgboost import XGBClassifier
import joblib

# --- import sibling preprocessing module ---
import sys
ROOT = Path(__file__).resolve().parents[1]  # project root (../307-Project)
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
from data_preprocessing import preprocessing as prep  # exposes preprocess()

app = Flask(__name__)

TRAIN_AUTH_TOKEN = os.getenv("TRAIN_AUTH_TOKEN")  # loaded from Secret

@app.before_request
def _guard_train():
    # Only protect training routes; /healthz and /metrics remain open inside cluster
    if request.path.startswith("/train"):
        if not TRAIN_AUTH_TOKEN:
            return  # no guard configured
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != TRAIN_AUTH_TOKEN:
            return jsonify({"error": "unauthorized"}), 401

# ---- Paths/Configs ----
DEFAULT_DATASET_PATH = "shared/data/diabetes_dataset00.csv"
LATEST_DATASET_PATH = "shared/data/latest_dataset.csv"
MODEL_DIR = os.getenv("MODEL_DIR", "shared/models")
PREP_OUTPUT_DIR = os.getenv("PREP_OUTPUT_DIR", "shared/clean")


def train_model_from_path(dataset_path: str):
    # Ensure dirs
    os.makedirs(PREP_OUTPUT_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Run preprocessing (ALL preprocessing is delegated here)
    try:
        report = prep.preprocess(
            input_path=dataset_path,
            output_dir=PREP_OUTPUT_DIR,
            target=os.getenv("PREP_TARGET", "Target") or None,
            scaling=os.getenv("PREP_SCALING", "standard"),
            drop_age_zero=os.getenv("PREP_DROP_AGE_ZERO", "true").lower() == "true",
            iqr_enabled=os.getenv("PREP_IQR_ENABLED", "true").lower() == "true",
            iqr_k=float(os.getenv("PREP_IQR_K", "1.5")),
            label_encode_target=os.getenv("PREP_LABEL_ENCODE_TARGET", "true").lower() == "true",
            save_csv=True,
            save_xlsx=False,
            save_report=True,
        )
    except Exception as e:
        raise RuntimeError(f"Preprocessing failed: {e}")

    outputs = report.get("outputs", {})
    X_path = outputs.get("X_csv") or outputs.get("X_xlsx")
    y_path = outputs.get("y_csv") or outputs.get("y_xlsx")

    if not X_path or not os.path.exists(X_path):
        raise FileNotFoundError("Preprocessing did not produce X features file.")
    if not y_path or not os.path.exists(y_path):
        raise FileNotFoundError("Preprocessing did not produce y labels file.")

    # Load clean features
    if X_path.endswith(".xlsx"):
        X_df = pd.read_excel(X_path)
        y_df = pd.read_excel(y_path)
    else:
        X_df = pd.read_csv(X_path)
        y_df = pd.read_csv(y_path)

    X = X_df.values
    y = np.ravel(y_df.values)

    # Split 70/15/15
    X_temp, X_test, y_temp, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_temp, y_temp, test_size=0.1765, random_state=42, stratify=y_temp
    )

    # Choose objective
    num_classes = len(np.unique(y))
    objective = "multi:softprob" if num_classes > 2 else "binary:logistic"

    # Train
    xgb = XGBClassifier(
        n_estimators=200,
        learning_rate=0.1,
        max_depth=8,
        objective=objective,
        eval_metric="mlogloss",
        use_label_encoder=False,
        n_jobs=-1,
        random_state=42,
    )
    xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=True)

    # Evaluate
    y_val_pred = xgb.predict(X_val)
    y_test_pred = xgb.predict(X_test)

    metrics = {
        "val": {
            "accuracy": float(accuracy_score(y_val, y_val_pred)),
            "f1_macro": float(f1_score(y_val, y_val_pred, average="macro")),
            "report": classification_report(y_val, y_val_pred, output_dict=True),
        },
        "test": {
            "accuracy": float(accuracy_score(y_test, y_test_pred)),
            "f1_macro": float(f1_score(y_test, y_test_pred, average="macro")),
            "report": classification_report(y_test, y_test_pred, output_dict=True),
        },
    }

    # Persist artifacts
    model_path = os.path.join(MODEL_DIR, "trained_model.pkl")
    joblib.dump(xgb, model_path)

    classes_info = report.get("label_encoder_on_target", {})
    classes_path = os.path.join(MODEL_DIR, "target_labels.json")
    with open(classes_path, "w", encoding="utf-8") as f:
        json.dump(classes_info, f, indent=2)

    prep_report_path = os.path.join(MODEL_DIR, "preprocessing_report.json")
    with open(prep_report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    metrics_path = os.path.join(MODEL_DIR, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return {
        "rows_train": int(X_train.shape[0]),
        "rows_val": int(X_val.shape[0]),
        "rows_test": int(X_test.shape[0]),
        "features": int(X_train.shape[1]),
        "artifacts": {
            "model_path": model_path,
            "classes_path": classes_path,
            "metrics_path": metrics_path,
            "preprocessing_report_path": prep_report_path,
            "X_path": X_path,
            "y_path": y_path,
        },
        "metrics_summary": {
            "val_accuracy": metrics["val"]["accuracy"],
            "val_f1_macro": metrics["val"]["f1_macro"],
            "test_accuracy": metrics["test"]["accuracy"],
            "test_f1_macro": metrics["test"]["f1_macro"],
        },
    }


# --- Health check ---
@app.route("/healthz", methods=["GET"])
def healthz():
    model_exists = os.path.exists(os.path.join(MODEL_DIR, "trained_model.pkl"))
    return jsonify({"status": "ok", "modelReady": model_exists}), 200


# --- Metrics endpoint ---
@app.route("/metrics", methods=["GET"])
def get_metrics():
    metrics_path = os.path.join(MODEL_DIR, "metrics.json")
    if not os.path.exists(metrics_path):
        return jsonify({"error": "metrics not found"}), 404
    with open(metrics_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data), 200


# --- Training routes ---
@app.route("/train", methods=["POST"])
def train():
    try:
        if "file" in request.files:
            raw = request.files["file"].read()
            os.makedirs(os.path.dirname(LATEST_DATASET_PATH), exist_ok=True)
            with open(LATEST_DATASET_PATH, "wb") as f:
                f.write(raw)
            dataset_path = LATEST_DATASET_PATH
        elif os.path.exists(LATEST_DATASET_PATH):
            dataset_path = LATEST_DATASET_PATH
        else:
            dataset_path = DEFAULT_DATASET_PATH

        info = train_model_from_path(dataset_path)
        return jsonify({"status": "Training complete", "info": info}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/train", methods=["GET"])
def retrain():
    try:
        dataset_path = (
            LATEST_DATASET_PATH if os.path.exists(LATEST_DATASET_PATH) else DEFAULT_DATASET_PATH
        )
        info = train_model_from_path(dataset_path)
        return jsonify({"status": "Retrained using last used/default dataset", "info": info}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------- CLI one-shot mode ----------
def cli_train(input_path, output_dir, model_dir, target, scaling,
              drop_age_zero, iqr_enabled, iqr_k, label_encode_target):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)

    # PREPROCESS quietly
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        report = prep.preprocess(
            input_path=input_path,
            output_dir=output_dir,
            target=target,
            scaling=scaling,
            drop_age_zero=drop_age_zero,
            iqr_enabled=iqr_enabled,
            iqr_k=iqr_k,
            label_encode_target=label_encode_target,
            save_csv=True,
            save_xlsx=False,
            save_report=True
        )

    outs = report.get("outputs", {})
    X_path = outs.get("X_csv") or outs.get("X_xlsx")
    y_path = outs.get("y_csv") or outs.get("y_xlsx")
    if not X_path or not y_path:
        raise RuntimeError("Preprocessing did not produce X/y files. Check input/flags.")

    # TRAIN
    if X_path.endswith(".xlsx"):
        X = pd.read_excel(X_path).values
        y = np.ravel(pd.read_excel(y_path).values)
    else:
        X = pd.read_csv(X_path).values
        y = np.ravel(pd.read_csv(y_path).values)

    X_tmp, X_test, y_tmp, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_tmp, y_tmp, test_size=0.1765, random_state=42, stratify=y_tmp
    )

    objective = "multi:softprob" if len(np.unique(y)) > 2 else "binary:logistic"
    model = XGBClassifier(
        n_estimators=200, learning_rate=0.1, max_depth=8,
        objective=objective, eval_metric="mlogloss",
        use_label_encoder=False, n_jobs=-1, random_state=42
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    yv = model.predict(X_val); yt = model.predict(X_test)
    metrics = {
        "val":  {"accuracy": float(accuracy_score(y_val, yv)),
                 "f1_macro": float(f1_score(y_val, yv, average="macro")),
                 "report": classification_report(y_val, yv, output_dict=True)},
        "test": {"accuracy": float(accuracy_score(y_test, yt)),
                 "f1_macro": float(f1_score(y_test, yt, average="macro")),
                 "report": classification_report(y_test, yt, output_dict=True)},
    }

    # SAVE
    model_path = os.path.join(model_dir, "trained_model.pkl")
    joblib.dump(model, model_path)
    with open(os.path.join(model_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    with open(os.path.join(model_dir, "preprocessing_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # SUMMARY
    rows_before = report.get("rows_before")
    rows_after  = report.get("rows_after")
    removed_total = (rows_before - rows_after) if (rows_before and rows_after) else None
    age_zero_removed = report.get("age_zero_rows_removed")
    iqr_removed = report.get("iqr_report", {}).get("rows_flagged_any_numeric")

    print("=== Preprocessing Summary ===")
    print(f"Input: {input_path}")
    print(f"Rows: {rows_before} -> {rows_after} (removed: {removed_total}, Age==0: {age_zero_removed}, IQR: {iqr_removed})")
    print(f"Features after OHE: {report.get('feature_count_after_ohe')}")
    print(f"Saved X,y to: {X_path}, {y_path}")

    print("\n=== Training Summary (XGBoost) ===")
    print(f"Splits: train={X_train.shape[0]}, val={X_val.shape[0]}, test={X_test.shape[0]}, d={X_train.shape[1]}")
    print(f"Val  Acc={metrics['val']['accuracy']:.4f} | F1_macro={metrics['val']['f1_macro']:.4f}")
    print(f"Test Acc={metrics['test']['accuracy']:.4f} | F1_macro={metrics['test']['f1_macro']:.4f}")
    print(f"Model: {model_path}")
    print(f"Metrics JSON: {os.path.join(model_dir, 'metrics.json')}")
    print(f"Prep report: {os.path.join(model_dir, 'preprocessing_report.json')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Training service")
    parser.add_argument("--mode", choices=["server", "train"], default="server",
                        help="server = run Flask API; train = run one-shot preprocess+train and exit")
    # CLI args for train mode (with sensible defaults)
    parser.add_argument("--input_path", default=os.getenv("PREP_INPUT_PATH", DEFAULT_DATASET_PATH))
    parser.add_argument("--prep_output_dir", default=os.getenv("PREP_OUTPUT_DIR", "shared/clean"))
    parser.add_argument("--model_dir", default=os.getenv("MODEL_DIR", "shared/models"))
    parser.add_argument("--target", default=os.getenv("PREP_TARGET", "Target"))
    parser.add_argument("--scaling", choices=["standard", "minmax", "none"],
                        default=os.getenv("PREP_SCALING", "standard"))
    parser.add_argument("--drop_age_zero", default=os.getenv("PREP_DROP_AGE_ZERO", "true"))
    parser.add_argument("--iqr_enabled", default=os.getenv("PREP_IQR_ENABLED", "true"))
    parser.add_argument("--iqr_k", type=float, default=float(os.getenv("PREP_IQR_K", "1.5")))
    parser.add_argument("--label_encode_target", default=os.getenv("PREP_LABEL_ENCODE_TARGET", "true"))

    args = parser.parse_args()

    if args.mode == "train":
        cli_train(
            input_path=args.input_path,
            output_dir=args.prep_output_dir,
            model_dir=args.model_dir,
            target=args.target,
            scaling=args.scaling,
            drop_age_zero=(str(args.drop_age_zero).lower() == "true"),
            iqr_enabled=(str(args.iqr_enabled).lower() == "true"),
            iqr_k=args.iqr_k,
            label_encode_target=(str(args.label_encode_target).lower() == "true"),
        )
    else:
        # Server mode
        app.run(host="0.0.0.0", port=8000)