<!--WRITE EVERYTHING ABOVE FIRST -->

## Extra Features In Kubernetes
-----

### Blue/Green Deployment Strategy for the UI

This project utilizes a **Blue/Green deployment strategy** for the user interface to ensure safer, zero-downtime releases. This approach involves running two identical, parallel production environments: "Blue" and "Green."

  - **Blue (`ui-blue`)**: Represents the current, stable version of the application that is live and serving user traffic.
  - **Green (`ui-green`)**: Represents the new version of the application. It runs in parallel but remains idle until it is ready to receive live traffic.

This strategy allows the new "Green" environment to be fully tested and validated in production without impacting users. When ready, traffic can be switched instantly from Blue to Green.



#### How It Works

The implementation relies on a few key Kubernetes components working together:

1.  **Two Parallel Deployments**:

      * A `ui-blue` deployment is configured with the label `version: blue`.
      * A `ui-green` deployment is configured with the label `version: green`.
      * Both deployments run the UI application and are otherwise identical in configuration.

2.  **A Single Service**:

      * A single Kubernetes Service named `ui` acts as the router for all incoming user traffic.
      * This service uses a `selector` to determine which deployment's pods should receive traffic. By default, it points to the `version: blue` pods.

3.  **Instant Traffic Switching**:

      * To switch traffic, the `scripts/switch-ui-color.sh` script is used.
      * This script executes a `kubectl patch` command that updates the `selector` on the `ui` service to point to the `version: green` pods.
      * This change is atomic, meaning users are instantly and seamlessly routed to the new version with no downtime.

-----

#### Why It Matters: The Benefits

This approach provides several significant advantages over traditional deployment methods like a standard `RollingUpdate`:

  * **Zero-Downtime Releases**: The cutover from Blue to Green is instantaneous. Kubernetes updates the service endpoints to only include ready pods from the target deployment.
  * **Instant Rollback**: If the new "Green" version has issues, rolling back is as simple as running the switch script again to point the service back to the "Blue" deployment. This is much faster and safer than a traditional rollback.
  * **Safer Deployments**: The "Green" environment can be warmed up and thoroughly tested in the production environment (e.g., smoke tests, health checks) before a single user is routed to it. This dramatically reduces the risk of deploying a faulty version.

-----

#### Operational Guide

Managing the Blue/Green deployments is straightforward using the provided script.

#### 1\. Switch Traffic to Green

To deploy a new version and switch live traffic to it, run the following command:

```bash
bash scripts/switch-ui-color.sh hospital-ml green
```

#### 2\. Verify the Switch

After running the script, you can verify that the `ui` service is now pointing to the "Green" deployment's pods:

```bash
kubectl -n hospital-ml get endpoints ui -o wide
```

You can also refresh the application in your browser to confirm the new version is live.

#### 3\. Rollback to Blue

If you encounter any issues with the "Green" deployment, you can instantly roll back traffic to the stable "Blue" version:

```bash
bash scripts/switch-ui-color.sh hospital-ml blue
```

-----

#### Built-in Safety: Readiness Probes

A critical safety feature is the use of **readiness probes** in the deployments.

  * If the "Green" deployment is unhealthy or fails its readiness checks, it will never be marked as "Ready."
  * As a result, it will not be added to the `ui` service's endpoints, even if the selector is switched.
  * In this scenario, the service continues to route traffic to the "Blue" pods, preventing users from ever hitting a broken application.

