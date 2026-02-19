# ─────────────────────────────────────────────────────────────────────────────
# RDS Scheduler Terraform Module
#
# Stops RDS instances outside business hours using Lambda + CloudWatch Events.
# Expected savings: ~65% for dev/test databases.
# ─────────────────────────────────────────────────────────────────────────────

variable "rds_instance_ids" {
  description = "List of RDS instance identifiers to schedule"
  type        = list(string)
}

variable "start_cron" {
  description = "Cron expression for starting instances (UTC)"
  type        = string
  default     = "cron(0 8 ? * MON-FRI *)"  # 8 AM UTC weekdays
}

variable "stop_cron" {
  description = "Cron expression for stopping instances (UTC)"
  type        = string
  default     = "cron(0 20 ? * MON-FRI *)"  # 8 PM UTC weekdays
}

variable "environment" {
  description = "Environment tag"
  type        = string
  default     = "dev"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM Role for Lambda
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "rds_scheduler" {
  name = "rds-scheduler-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    "cost-optimizer" = "rds-scheduler"
    "environment"    = var.environment
  }
}

resource "aws_iam_role_policy" "rds_scheduler" {
  name = "rds-scheduler-policy"
  role = aws_iam_role.rds_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds:StopDBInstance",
          "rds:StartDBInstance",
          "rds:DescribeDBInstances"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda Function — Stop RDS
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "stop_lambda" {
  type        = "zip"
  output_path = "${path.module}/stop_rds.zip"

  source {
    content  = <<-PYTHON
import boto3
import os

def handler(event, context):
    rds = boto3.client('rds')
    instance_ids = os.environ['RDS_INSTANCE_IDS'].split(',')
    
    for instance_id in instance_ids:
        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            status = response['DBInstances'][0]['DBInstanceStatus']
            
            if status == 'available':
                rds.stop_db_instance(DBInstanceIdentifier=instance_id)
                print(f'Stopped: {instance_id}')
            else:
                print(f'Skipped {instance_id}: status={status}')
        except Exception as e:
            print(f'Error stopping {instance_id}: {e}')
    
    return {'statusCode': 200, 'body': f'Processed {len(instance_ids)} instances'}
PYTHON
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "stop_rds" {
  function_name = "rds-scheduler-stop"
  role          = aws_iam_role.rds_scheduler.arn
  handler       = "lambda_function.handler"
  runtime       = "python3.11"
  timeout       = 60

  filename         = data.archive_file.stop_lambda.output_path
  source_code_hash = data.archive_file.stop_lambda.output_base64sha256

  environment {
    variables = {
      RDS_INSTANCE_IDS = join(",", var.rds_instance_ids)
    }
  }

  tags = {
    "cost-optimizer" = "rds-scheduler"
    "action"         = "stop"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda Function — Start RDS
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "start_lambda" {
  type        = "zip"
  output_path = "${path.module}/start_rds.zip"

  source {
    content  = <<-PYTHON
import boto3
import os

def handler(event, context):
    rds = boto3.client('rds')
    instance_ids = os.environ['RDS_INSTANCE_IDS'].split(',')
    
    for instance_id in instance_ids:
        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            status = response['DBInstances'][0]['DBInstanceStatus']
            
            if status == 'stopped':
                rds.start_db_instance(DBInstanceIdentifier=instance_id)
                print(f'Started: {instance_id}')
            else:
                print(f'Skipped {instance_id}: status={status}')
        except Exception as e:
            print(f'Error starting {instance_id}: {e}')
    
    return {'statusCode': 200, 'body': f'Processed {len(instance_ids)} instances'}
PYTHON
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "start_rds" {
  function_name = "rds-scheduler-start"
  role          = aws_iam_role.rds_scheduler.arn
  handler       = "lambda_function.handler"
  runtime       = "python3.11"
  timeout       = 60

  filename         = data.archive_file.start_lambda.output_path
  source_code_hash = data.archive_file.start_lambda.output_base64sha256

  environment {
    variables = {
      RDS_INSTANCE_IDS = join(",", var.rds_instance_ids)
    }
  }

  tags = {
    "cost-optimizer" = "rds-scheduler"
    "action"         = "start"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch Event Rules (Cron Triggers)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "stop_schedule" {
  name                = "rds-stop-schedule"
  description         = "Stop RDS instances outside business hours"
  schedule_expression = var.stop_cron

  tags = {
    "cost-optimizer" = "rds-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "stop_target" {
  rule = aws_cloudwatch_event_rule.stop_schedule.name
  arn  = aws_lambda_function.stop_rds.arn
}

resource "aws_lambda_permission" "stop_permission" {
  statement_id  = "AllowCloudWatchStop"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stop_rds.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stop_schedule.arn
}

resource "aws_cloudwatch_event_rule" "start_schedule" {
  name                = "rds-start-schedule"
  description         = "Start RDS instances at beginning of business hours"
  schedule_expression = var.start_cron

  tags = {
    "cost-optimizer" = "rds-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "start_target" {
  rule = aws_cloudwatch_event_rule.start_schedule.name
  arn  = aws_lambda_function.start_rds.arn
}

resource "aws_lambda_permission" "start_permission" {
  statement_id  = "AllowCloudWatchStart"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.start_rds.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.start_schedule.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────

output "stop_lambda_arn" {
  description = "ARN of the stop Lambda function"
  value       = aws_lambda_function.stop_rds.arn
}

output "start_lambda_arn" {
  description = "ARN of the start Lambda function"
  value       = aws_lambda_function.start_rds.arn
}

output "estimated_savings" {
  description = "Estimated savings from scheduling"
  value       = "~65% cost reduction for scheduled instances (12h/day off + weekends)"
}
