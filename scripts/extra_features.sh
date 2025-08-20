#!/bin/bash
set -e

NAMESPACE="hospital-ml"

# Make sure all scripts are executable
chmod +x scripts/*.sh

echo "🚀 Running Blue/Green setup..."
bash scripts/bluegreen-setup.sh $NAMESPACE green

echo "📜 Checking rollout history for model-inference..."
bash scripts/rollout-ctl.sh $NAMESPACE model-inference history

echo "🔒 Applying security baseline (NetworkPolicies)..."
bash scripts/netpol-apply.sh $NAMESPACE

echo "💾 Installing backup tools..."
bash scripts/backup-tools.sh $NAMESPACE install

echo "🕒 Running an immediate backup..."
bash scripts/backup-tools.sh $NAMESPACE run-now

echo "📂 Listing available backups..."
bash scripts/backup-tools.sh $NAMESPACE list

echo "✅ All extra features applied successfully!"