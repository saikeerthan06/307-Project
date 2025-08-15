from flask import Flask, request, jsonify
import pandas as pd
import numpy as np
import joblib
import os

app = Flask(__name__)

# Paths to shared model artifacts
MODEL_DIR = "/shared/models"
MODEL_PATH = os.path.join(MODEL_DIR, "trained_model.pkl")
ENCODER_PATH = os.path.join(MODEL_DIR, "encoder_ohe.pkl")
LABELS_PATH = os.path.join(MODEL_DIR, "target_labels.pkl")
CAT_COLS_PATH = os.path.join(MODEL_DIR, "categorical_cols.pkl")
NUM_COLS_PATH = os.path.join(MODEL_DIR, "numerical_cols.pkl")

# Load artifacts once at startup
try:
    model = joblib.load(MODEL_PATH)
    ohe = joblib.load(ENCODER_PATH)
    labels = joblib.load(LABELS_PATH)
    categorical_cols = joblib.load(CAT_COLS_PATH)
    numerical_cols = joblib.load(NUM_COLS_PATH)
    print("Model inference artifacts loaded.")
except Exception as e:
    print(f"Failed to load artifacts: {e}")
    model = ohe = labels = categorical_cols = numerical_cols = None

@app.route('/')
def home():
    return " Model Inference API is running."

@app.route('/infer', methods=['POST'])
def infer():
    if None in [model, ohe, labels, categorical_cols, numerical_cols]:
        return jsonify({'error': 'Model artifacts not loaded'}), 500

    try:
        input_json = request.get_json()
        df = pd.DataFrame([input_json])

        X_cat = ohe.transform(df[categorical_cols])
        X_num = df[numerical_cols].values
        X = np.hstack([X_num, X_cat])

        pred_class = model.predict(X)[0]
        pred_label = labels[pred_class]

        return jsonify({'prediction': str(pred_label)})

    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
