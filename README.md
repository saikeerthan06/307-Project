# NeuroNova Project ReadMe - Kubernetes

## Project Details & Objective

This `README.md` file conveys every single aspect of this Kubernetes Project with the main Project objective in mind is to deploy an end to end AI Application in Kubernetes for Diabetes Detection. 

In the real world scenarios, this project can be proved to be a crucial aspect in hospitals:


1. **Early and Proactive Healthcare**: A reliable diabetes prediction tool can help identify individuals at high risk of developing the disease. This allows for early intervention through lifestyle changes, diet modifications, and preventative medical care, potentially delaying or even preventing the onset of diabetes.

2. **Reduced Healthcare Costs**: Early detection and prevention of chronic diseases like diabetes can significantly reduce long-term healthcare costs for both individuals and the healthcare system as a whole. Fewer complications and hospitalizations lead to lower medical expenses.

3. **Scalable and Reliable Health Tech**: The use of a microservices architecture and Kubernetes for deployment means the application can be scaled to handle a large number of users and is resilient to failures. This is crucial for real-world healthcare applications that need to be highly available and reliable.

---

## Kubernetes 

Kubernetes is used to run our application, making it reliable, scalable, and easy to manage. Instead of running our code on a single machine, Kubernetes orchestrates multiple "containers" to work together seamlessly. Coupled with Docker, the orchestration by Kubernetes provides a symphonic harmony in allowing for the Application to be **reliable**, **scalable** & **modular**. 

The Architecture of our project is what is shown in the image:
![architecture](kubernetes-architecture.png)

**The Architecture had proven to be robust through the following ways:**
1. User Access: A user sends a request to our application from their browser over the internet.

2. Ingress Routing: The request first hits the Ingress Controller, which acts as the main entry point to our system. It securely routes the traffic to the correct service, which in this case is our User Interface (UI).

3. Frontend Service (UI): The request is forwarded to the UI Service, which manages and distributes the load across multiple running copies (Pods) of our UI application. This ensures the frontend is always available and responsive.

4. Backend Service (Model Inference): To make a prediction, the UI application communicates with the Model Inference Service. This backend service also manages multiple Pods, each capable of running our trained machine learning model to predict diabetes based on the data provided.

5. Shared Storage: All the different parts of our application are connected to a Shared Persistent Volume. This is where we store our dataset, the trained machine learning models, and backups. This ensures that all components have consistent access to the data they need.

6. Automated Tasks: We run two automated jobs on a nightly schedule:

7. Nightly Retrain: This job automatically retrains our ML model with new data to improve its accuracy over time.

8. Nightly Backup: This job creates a backup of our models and data to prevent data loss

## Kubernetes Modules: 

##### Universal Files across all modules:
1. **requirements.txt**:
    - Defines Python dependencies required by the UI.
    - Ensures reproducibility & consistency across environments. 
2. **Dockerfile**:
    - Specifies how the Container should be built through a lightweight Python base image. 
    - Enables Kubernetes to run the UI as an isolated, reproducible container. 

A kubernetes architecture has to be modular, therefore we have intelligently split the entire project into different modules, allowing each module to be scaled, rolled out and more independently. 

The modules that we have split into are: 



1. ### User-Interface (UI):

    - The UI Module serves as the front foor of the Kubernetes Project, providing a seamless way for users to interact with the deployed application. 
    - Kubernetes has full control of the orchestration of this module, containerised by Docker, designed for scalability, reliability and security in mind. 

    #### CORE COMPONENTS:

    


    ##### Python Files:
    1. **app.py**:
        - Serves as the main entry point to the UI Service.
        - Developed with Streamlit and FastAPI
        - Handles the routing of the HTTPS requests and integrates with backend services.
    2. **client.py**:
        - Acts as a connector between UI & Model Inference Service 
        - Sends requests to the model-inference-svc and retrieves predictions of the model. 

2. ### Data-Preprocessing:
3. ### Model Training:
4. ### Model Inference: 


