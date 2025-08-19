## 1) Blue/Green UI Cutover

What it is: Two complete UI Deployments (ui-blue, ui-green) run in parallel behind one Service/ui. We flip traffic by changing the Service selector version=blue|green.

Why it matters: Safer releases than a standard RollingUpdate; we can warm up green, run smoke checks, then switch instantly. Rollback is just switching back to blue. Zero downtime because Endpoints update atomically to ready pods only (readiness probes guard this).

How we operate it:

Flip: bash scripts/switch-ui-color.sh hospital-ml green (or blue).

Verify: kubectl -n hospital-ml get endpoints ui -o wide and refresh the browser.

If green is unhealthy: readiness fails ⇒ no Endpoints ⇒ Service serves nothing; switch back to blue immediately.

## 2) Rollout history / pause / resume / undo (Deployment controls)

What it is: The Deployment controller tracks versions as ReplicaSets. With kubectl set image we roll forward; with rollout history we view revisions; and with rollout undo we return to a previous RS. We can pause mid-rollout to run checks, then resume.

Why it matters: Clean, audited changes with instant rollback. This is the “production way” to upgrade stateless services.

How we operate it:

Change cause: kubectl annotate deploy ... kubernetes.io/change-cause="..."

Roll forward: kubectl set image deploy/X ... + kubectl rollout status

Inspect: kubectl rollout history deploy/X

Pause/Resume: kubectl rollout pause|resume deploy/X

Undo: kubectl rollout undo deploy/X

Where we demo: On model-inference (low blast radius).

## 3) Default-Deny NetworkPolicy + least-privilege egress

What it is: We applied a namespace-wide default-deny policy (blocks all pod ingress/egress). Then we added only the minimal allows our app needs:

UI ingress only from ingress-nginx on the UI port.

UI egress only to backends (DP/Training/Inference) on port 8000.

DNS egress to kube-dns (TCP/UDP 53) so name resolution works.

Backend policies allow ingress from UI.

Why it matters: Principle of least privilege; a compromised UI pod cannot reach the internet or random cluster pods.

How we prove it:

Positive: wget -qO- data-preprocessing-svc:8000/healthz returns JSON.

Negative: wget -qO- https://example.com fails ⇒ “blocked”.

Gotchas we handled: DNS would break under default-deny; we explicitly allowed to kube-dns.

## 4) Nightly Backup CronJob (DR)

What it is: A CronJob runs 02:00 daily, tarring /shared/models and /shared/data/clean into /shared/models/artifacts/backups/backup-<timestamp>.tgz.

Why it matters: Disaster recovery: if a deploy or human error wipes a model, we can restore from the PVC.

How we operate it:

On-demand run: kubectl create job backup-now --from=cronjob/nightly-backup

Verify logs: kubectl logs -f job/backup-now

See file: ls /shared/models/artifacts/backups from any app pod.

Copy out: kubectl cp deploy/ui-blue:/shared/models/artifacts/backups/<file>.tgz ./

Restore story (talk track): Extract the tar back onto /shared; inference reads the .joblib model as before; no retrain required.

## How these map to SRE/K8s fundamentals (talking points)

Service + Endpoints = live traffic switch. Our Blue/Green flip changes only the Service selector; kube-proxy updates Endpoints immediately for ready pods, so users don’t see errors.

Deployments manage RS history. Each image change creates a new ReplicaSet; history shows revisions; undo swaps active RS.

NetworkPolicy is enforced by the CNI. With default-deny, pods talk only where explicitly allowed. We carved out just three flows: Ingress-NGINX→UI, UI→Backends, and any pod→DNS.

CronJobs create Jobs. Each run is an immutable Job/Pod that writes a timestamped backup; data durability is via our PVC.

## Likely Q&A (crisp answers)

Q: Why Blue/Green over a RollingUpdate?
A: Blue/Green gives instant, atomic cutover and instant rollback, and lets us validate green fully before switching real traffic. RollingUpdate is gradual; Blue/Green is binary.

Q: What protects users from a bad Green?
A: Readiness probes. If green isn’t ready, it never appears in Service Endpoints; we keep serving blue. We also verify and can flip back in one command.

Q: How do you know what changed in a rollout?
A: We set kubernetes.io/change-cause and use kubectl rollout history to see annotated revisions.

Q: Won’t default-deny break DNS?
A: Yes, unless allowed. We explicitly allow egress to kube-dns on TCP/UDP 53.

Q: Can the UI call the open internet now?
A: No. Egress is restricted to backends on port 8000 plus DNS only. An external wget from the UI pod fails.

Q: Where are backups stored and how long?
A: Under /shared/models/artifacts/backups on our PVC. Retention is “keep all” right now; easy to add a prune CronJob if desired.

Q: How would you restore after data loss?
A: Copy the latest .tgz back to /shared and extract; inference reads the saved .joblib immediately.

Q: Could you do canary instead of Blue/Green?
A: Yes—Ingress-NGINX supports canary annotations to split traffic percentage-wise. We picked Blue/Green for a crisp, visual cutover in the demo.