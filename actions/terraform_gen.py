"""
Terraform HCL generator powered by Claude AI.

Takes an anomaly + RAG context and calls the Claude API to produce a
structured recommendation including:
- Root cause analysis
- Recommended actions
- Generated Terraform HCL code
- Savings estimate
- Risk level
- Rollback plan

Usage
-----
    from actions.terraform_gen import generate_recommendation
    rec = generate_recommendation(anomaly, context)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic

from config.settings import settings
from detect.models import Anomaly, Recommendation, RiskLevel

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Prompt Template
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior cloud infrastructure engineer specializing in AWS cost optimization.
You receive anomaly data from an automated detection system along with relevant
documentation context from a RAG knowledge base.

Your job is to:
1. Analyze the root cause of the cost anomaly or waste pattern.
2. Recommend specific, actionable steps to reduce costs.
3. Generate working Terraform HCL code that implements the fix.
4. Estimate the monthly savings in USD.
5. Assess the risk level (low / medium / high).
6. Provide a rollback plan.

IMPORTANT:
- The Terraform code MUST be valid, complete HCL that can be applied directly.
- Use variables for any values that should be configurable.
- Include relevant tags for cost tracking.
- Always respond in the exact JSON format specified below.
"""

_USER_PROMPT_TEMPLATE = """\
ANOMALY DETAILS:
  Service: {service}
  Resource: {resource_id}
  Issue Type: {issue_type}
  Current Cost: ${current_cost:.2f}/month
  Expected Cost: ${expected_cost:.2f}/month
  Waste Score: {waste_score}/100
  Metrics: {metrics}
  Account: {account}
  Region: {region}

RELEVANT DOCUMENTATION:
{context}

Respond in this exact JSON format:
{{
  "root_cause": "Detailed explanation of why this anomaly occurred",
  "actions": ["Step 1 description", "Step 2 description", ...],
  "terraform_code": "Full HCL code block as a single string",
  "savings_estimate": 123.45,
  "risk_level": "low|medium|high",
  "rollback_plan": "Step-by-step rollback instructions",
  "confidence": 0.85
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Claude API Call
# ──────────────────────────────────────────────────────────────────────────────


def _call_claude(anomaly: Anomaly, context: str) -> dict[str, Any]:
    """
    Call Claude API with the anomaly + context prompt.

    Returns the parsed JSON response dict.
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        service=anomaly.service,
        resource_id=anomaly.resource_id or "N/A",
        issue_type=anomaly.issue_type.value,
        current_cost=anomaly.current_cost,
        expected_cost=anomaly.expected_cost,
        waste_score=anomaly.waste_score,
        metrics=json.dumps(anomaly.metrics, indent=2),
        account=anomaly.account or "N/A",
        region=anomaly.region or settings.AWS_DEFAULT_REGION,
        context=context,
    )

    logger.info("Calling Claude API for %s anomaly on %s", anomaly.issue_type.value, anomaly.service)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Extract text from response
    response_text = message.content[0].text

    # Parse JSON from response (handle potential markdown code blocks)
    json_match = re.search(r"\{[\s\S]*\}", response_text)
    if json_match:
        return json.loads(json_match.group())

    raise ValueError(f"Could not parse JSON from Claude response: {response_text[:200]}")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def generate_recommendation(anomaly: Anomaly, context: str) -> Recommendation:
    """
    Generate a full optimization recommendation for an anomaly.

    Calls Claude with the anomaly data + RAG context and returns a structured
    :class:`Recommendation` object.

    Parameters
    ----------
    anomaly : Anomaly
        The detected anomaly.
    context : str
        RAG-retrieved context string.

    Returns
    -------
    Recommendation
        Structured recommendation with Terraform code, savings, risk, etc.
    """
    response = _call_claude(anomaly, context)

    # Parse risk level
    risk_str = response.get("risk_level", "medium").lower()
    try:
        risk = RiskLevel(risk_str)
    except ValueError:
        risk = RiskLevel.MEDIUM

    recommendation = Recommendation(
        anomaly=anomaly,
        root_cause=response.get("root_cause", "Unable to determine root cause"),
        actions=response.get("actions", []),
        terraform_code=response.get("terraform_code", ""),
        savings_estimate=float(response.get("savings_estimate", 0)),
        risk_level=risk,
        rollback_plan=response.get("rollback_plan", "Revert the Terraform change and apply"),
        confidence=float(response.get("confidence", 0.5)),
    )

    logger.info(
        "Generated recommendation: savings=$%.2f/mo, risk=%s, confidence=%.0f%%",
        recommendation.savings_estimate,
        recommendation.risk_level.value,
        recommendation.confidence * 100,
    )
    return recommendation


def generate_terraform_only(anomaly: Anomaly, context: str) -> str:
    """
    Convenience wrapper that returns only the generated Terraform HCL code.
    """
    rec = generate_recommendation(anomaly, context)
    return rec.terraform_code
