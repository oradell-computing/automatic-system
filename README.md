# Secure Serverless API Architecture (AWS CDK & Python)

I build production-grade, end-to-end serverless architectures with AWS. 

This example Lambda/RDS project demonstrates some of the secure, infrastructure-as-code (IaC) principles I adhere to. It strictly isolates the database from the internet using a well-planned VPC network, and it implements least-privilege IAM access. All projects are bespoke to manage compute spend. 

## Core Infrastructure Components

1. **VPC & Networking:** A custom VPC featuring isolated private subnets to segregate database workloads from the public internet.
2. **Compute & Routing:** A hardened API Gateway routes incoming requests to a Lambda function written in Python.
3. **Data Tier:** An RDS instance deployed entirely within the private subnets, completely inaccessible from the public internet.
4. **Access Control & Security:**
   * **Least-Privilege IAM:** The application strictly enforces isolated execution roles; compute resources only hold the exact permissions required to function.
   * **Client-Controlled Access:** When collaborating with agencies or clients, all infrastructure changes and deployments are managed through strict role-based access control (RBAC), ensuring external engineers only retain temporary, audited access for the duration required.

## Project Structure - try it yourself!

```
├── app.py                  # CDK App entry point
├── infrastructure/
│   ├── vpc_stack.py        # Custom VPC, subnets, and security groups
│   ├── database_stack.py   # RDS instance and subnet groups
│   └── compute_stack.py    # API Gateway, Lambda, and IAM policies
└── runtime/
    └── handler.py          # Python Lambda handler code
```

## **Key Engineering Highlights**

- **Infrastructure as Code**: Entirely defined and provisioned via AWS CDK in Python, eliminating manual console drift.
- **Security Best Practices**: Zero public exposure for the database layer; secure handling of environment variables and secrets via tight IAM boundaries.
- **Modular Design**: Stack separation ensures that networking, data, and compute layers are cleanly decoupled for maintainability.
