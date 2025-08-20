"""
Model Training Service (FastAPI) — Kubernetes-friendly, replica-safe job tracking.

Endpoints
---------
GET  /healthz
POST /train
    {
      "input_path": "/shared/data/clean/diabetes_dataset00_clean.csv",
      "target_column": "Target",
      "test_size": 0.2,
      "val_size": 0.2,
      "random_state": 42,
      "stratify": true,
      "xgb_params": {
        "n_estimators": 300,
        "learning_rate": 0.05,
        "max_depth": 6,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_jobs": 0
      },
      "early_stopping_rounds": 20,
      "save_as": "xgb_model.joblib",  
      "persist_metrics": true
    }

GET  /train/{job_id}

Auth (optional)
---------------
If TRAIN_REQUIRE_AUTH=true (env), the service expects header 'X-Train-Token' matching TRAIN_AUTH_TOKEN.
By default TRAIN_REQUIRE_AUTH=false to keep your current UI flow working without header wiring.
"""

from __future__ import annotations

import os
import json
import uuid
import time
import traceback
from typing import Optional, Dict, Any, Literal

import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Request
from pydantic import BaseModel, Field

from train import train_xgb, ensure_dirs

RAW_DIR   = os.getenv("RAW_DIR", "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")
ARTIFACT_DIR = os.path.join(MODEL_DIR, "artifacts")
JOBS_DIR = os.path.join(ARTIFACT_DIR, "jobs")

TRAIN_TOKEN = os.getenv("TRAIN_AUTH_TOKEN", "")
REQUIRE_AUTH = os.getenv("TRAIN_REQUIRE_AUTH", "false").lower() == "true"

app = FastAPI(title="Model Training Service", version="1.0.0")


# ---------- Pydantic models ----------

class TrainRequest(BaseModel):
    input_path: str = Field(..., description="Absolute path to CLEAN CSV (output of preprocessing)")
    target_column: Optional[str] = "Target"
    test_size: float = 0.2
    val_size: float = 0.2
    random_state: int = 42
    stratify: bool = True

    xgb_params: Dict[str, Any] = Field(
        default_factory=lambda: {
            "n_estimators": 300,
            "learning_rate": 0.05,
            "max_depth": 6,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "n_jobs": 0,  # use all cores in container; xgboost treats 0 as 'all'
            "tree_method": "hist",
            "objective": "multi:softprob"  # dataset has 13 classes; will auto-infer num_class
        }
    )
    early_stopping_rounds: Optional[int] = 20
    save_as: Optional[str] = None
    persist_metrics: bool = True


class TrainStatus(BaseModel):
    job_id: Optional[str] = None
    state: Literal["queued", "running", "succeeded", "failed"]
    model_path: Optional[str] = None
    report_path: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    trace: Optional[str] = None


# ---------- Job store (replica-safe via files) ----------

def _job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _write_job(job: Dict[str, Any]) -> None:
    ensure_dirs([JOBS_DIR])
    tmp = _job_path(job["job_id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _job_path(job["job_id"]))


def _read_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- Auth ----------

def _enforce_auth(x_train_token: Optional[str]) -> None:
    if REQUIRE_AUTH:
        if not TRAIN_TOKEN or x_train_token != TRAIN_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- Routes ----------

@app.get("/healthz")
def healthz():
    try:
        ensure_dirs([RAW_DIR, CLEAN_DIR, MODEL_DIR, ARTIFACT_DIR, JOBS_DIR])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dir issue: {e}")
    return {"status": "ok"}


@app.post("/train", response_model=TrainStatus)
def start_train(req: TrainRequest, background: BackgroundTasks, x_train_token: Optional[str] = Header(None)):
    _enforce_auth(x_train_token)

    # Basic path checks
    if not os.path.isabs(req.input_path):
        raise HTTPException(status_code=400, detail="input_path must be absolute")
    if not os.path.exists(req.input_path):
        raise HTTPException(status_code=404, detail=f"input_path not found: {req.input_path}")

    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "state": "queued",
        "model_path": None,
        "report_path": None,
        "report": None,
        "error": None,
        "trace": None,
        "started_at": time.time(),
        "request": req.model_dump(),
    }
    _write_job(job)

    background.add_task(_run_train, job_id, req)
    return TrainStatus(**job)


@app.get("/train/{job_id}", response_model=TrainStatus)
def get_train(job_id: str):
    job = _read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    return TrainStatus(**job)


# ---------- Background worker ----------

def _run_train(job_id: str, req: TrainRequest) -> None:
    job = _read_job(job_id) or {"job_id": job_id, "state": "running"}
    job["state"] = "running"
    _write_job(job)

    try:
        ensure_dirs([MODEL_DIR, ARTIFACT_DIR])
        result = train_xgb(
            csv_path=req.input_path,
            target_col=req.target_column,
            test_size=req.test_size,
            val_size=req.val_size,
            random_state=req.random_state,
            stratify=req.stratify,
            xgb_params=req.xgb_params,
            early_stopping_rounds=req.early_stopping_rounds,
            model_dir=MODEL_DIR,
            artifacts_dir=ARTIFACT_DIR,
            save_as=req.save_as,
            persist_metrics=req.persist_metrics,
        )

        job.update({
            "state": "succeeded",
            "model_path": result.get("model_path"),
            "report_path": result.get("report_path"),
            "report": result.get("report", None),
        })
        _write_job(job)

    except Exception as e:
        job.update({
            "state": "failed",
            "error": str(e),
            "trace": traceback.format_exc(limit=10),
        })
        _write_job(job)
