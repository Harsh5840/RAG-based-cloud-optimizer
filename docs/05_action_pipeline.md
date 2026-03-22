# 05 - Action Pipeline & LLM Engineering

The `actions/` directory acts as the system's "hands." Once an anomaly is found and contextualized by RAG, this pipeline kicks in to fix the issue. The actions are orchestrated sequentially.

## 1. Claude Terraform Generator (`terraform_gen.py`)

The `generate_recommendation()` function is the brain of the fix.
It structures a massive `System Prompt` for `claude-sonnet-4`. 

### The Request Payload
It injects the following into Anthropic's API prompt:
- **Anomaly Context:** JSON containing exact cost, expected cost, resource ID, and calculated waste score.
- **RAG Context:** The 5 raw documentation chunks provided by Pinecone.

### Output
The Claude API returns a **Structured JSON response** containing:
1. `root_cause`: Why was this resource flagged?
2. `actions`: Array of step-by-step required fixes.
3. `terraform_code`: A completely valid HCL block adjusting the deployment.
4. `savings_estimate`: A float USD prediction.
5. `risk_level`: Low / Medium / High assessment.
6. `rollback_plan`: Safe exit instructions.

## 2. GitHub PR Automation (`github_pr.py`)

Creating fully automated Pull Requests allows infrastructure engineers to simply review and merge changes. `create_optimization_pr()` utilizes `PyGithub`:

1. It checks out the main repository and creates a feature branch (`cost-opt/service-resource_id-timestamp`).
2. It pushes the string `terraform_code` from Claude into a logical path: `terraform/aws_optimization/instance_id.tf`.
3. It creates a robust PR template pulling in all the fields (Savings, Root cause, Risks) directly into the GitHub PR body.
4. Returns the `pr.html_url`.

## 3. Slack Delivery (`slack_notify.py`)

Using generic WebHooks (`requests.post`), `send_notification()` constructs interactive **Slack Block Kit** UI payloads. It uses logic to determine the emoji mapping (e.g. 🐘 for Overprovisioned) and links the provided `pr.html_url` directly onto a Slack button for instant 1-click reviews.
