#!/usr/bin/env python3
"""Safely remove AWS resources created for the customer-support agent project.

The script is dry-run only unless both ``--execute`` and the exact account
confirmation value are provided. It does not explicitly delete additional IAM
roles; ``agentcore destroy`` may remove its unused runtime execution role.

Examples:
    python cleanup_aws.py
    python cleanup_aws.py --execute --confirm DELETE-978050722836
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError


EXPECTED_ACCOUNT_ID = "978050722836"
DEFAULT_REGION = "us-east-1"


@dataclass(frozen=True)
class Resources:
    agent_name: str = "customer_support_agent"
    gateway_id: str = "customersupportgateway-zxwleqcenx"
    memory_id: str = "CustomerSupportMemory-kWL6JCEqG2"
    knowledge_base_id: str = "XLBKLUHVXU"
    s3_bucket: str = "customer-support-kb-978050722836"
    rest_api_id: str = "4ohqmkxpg5"
    lambda_functions: tuple[str, ...] = ("order-tracker", "refund-processor")


class Cleanup:
    """Run the project cleanup in dependency-safe order."""

    def __init__(self, region: str, execute: bool) -> None:
        self.region = region
        self.execute = execute
        self.session = boto3.Session(region_name=region)
        self.agentcore = self.session.client("bedrock-agentcore-control")
        self.bedrock = self.session.client("bedrock-agent")
        self.aoss = self.session.client("opensearchserverless")
        self.s3 = self.session.client("s3")
        self.apigateway = self.session.client("apigateway")
        self.lambda_client = self.session.client("lambda")

    @property
    def label(self) -> str:
        return "DELETE" if self.execute else "DRY-RUN"

    def announce(self, message: str) -> None:
        print(f"[{self.label}] {message}")

    @staticmethod
    def is_missing(error: ClientError) -> bool:
        code = error.response.get("Error", {}).get("Code", "")
        return code in {
            "ResourceNotFoundException",
            "NotFoundException",
            "NoSuchBucket",
            "NoSuchEntity",
        }

    def destroy_runtime(self, resources: Resources) -> None:
        command = [
            "agentcore",
            "destroy",
            "--agent",
            resources.agent_name,
            "--force",
        ]
        self.announce("AgentCore runtime: " + " ".join(command))
        if not self.execute:
            return

        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print(
                "[WARNING] AgentCore CLI cleanup failed. Continue with the "
                "remaining resources, then run the command manually.",
                file=sys.stderr,
            )

    def delete_gateway(self, resources: Resources) -> None:
        self.announce(
            f"Gateway targets and Gateway {resources.gateway_id}"
        )
        if not self.execute:
            return

        try:
            paginator = self.agentcore.get_paginator("list_gateway_targets")
            for page in paginator.paginate(
                gatewayIdentifier=resources.gateway_id
            ):
                for target in page.get("items", []):
                    target_id = target["targetId"]
                    print(f"  deleting Gateway target {target_id}")
                    self.agentcore.delete_gateway_target(
                        gatewayIdentifier=resources.gateway_id,
                        targetId=target_id,
                    )

            self.agentcore.delete_gateway(
                gatewayIdentifier=resources.gateway_id
            )
        except ClientError as error:
            if self.is_missing(error):
                print("  Gateway already absent")
            else:
                raise

    def delete_memory(self, resources: Resources) -> None:
        self.announce(f"AgentCore Memory {resources.memory_id}")
        if not self.execute:
            return

        try:
            self.agentcore.delete_memory(memoryId=resources.memory_id)
        except ClientError as error:
            if self.is_missing(error):
                print("  Memory already absent")
            else:
                raise

    def delete_knowledge_base(self, resources: Resources) -> str | None:
        self.announce(
            f"Knowledge Base and data sources {resources.knowledge_base_id}"
        )
        if not self.execute:
            self.announce(
                "OpenSearch collection will be discovered from the KB"
            )
            return None

        collection_id = None
        try:
            knowledge_base = self.bedrock.get_knowledge_base(
                knowledgeBaseId=resources.knowledge_base_id
            )["knowledgeBase"]
            collection_arn = (
                knowledge_base.get("storageConfiguration", {})
                .get("opensearchServerlessConfiguration", {})
                .get("collectionArn")
            )
            if collection_arn:
                collection_id = collection_arn.rsplit("/", 1)[-1]
                print(f"  discovered OpenSearch collection {collection_id}")

            paginator = self.bedrock.get_paginator("list_data_sources")
            for page in paginator.paginate(
                knowledgeBaseId=resources.knowledge_base_id
            ):
                for data_source in page.get("dataSourceSummaries", []):
                    data_source_id = data_source["dataSourceId"]
                    print(f"  deleting data source {data_source_id}")
                    self.bedrock.delete_data_source(
                        knowledgeBaseId=resources.knowledge_base_id,
                        dataSourceId=data_source_id,
                    )

            self.bedrock.delete_knowledge_base(
                knowledgeBaseId=resources.knowledge_base_id
            )
            self.wait_for_knowledge_base_deletion(
                resources.knowledge_base_id
            )
        except ClientError as error:
            if self.is_missing(error):
                print("  Knowledge Base already absent")
            else:
                raise

        return collection_id

    def wait_for_knowledge_base_deletion(self, knowledge_base_id: str) -> None:
        for _ in range(18):
            try:
                self.bedrock.get_knowledge_base(
                    knowledgeBaseId=knowledge_base_id
                )
            except ClientError as error:
                if self.is_missing(error):
                    return
                raise
            time.sleep(10)
        print(
            "[WARNING] Knowledge Base deletion is still in progress; "
            "rerun the script later if collection deletion fails."
        )

    def delete_collection(self, collection_id: str | None) -> None:
        if not collection_id:
            self.announce(
                "OpenSearch collection not identified; verify it manually"
            )
            return

        self.announce(f"OpenSearch Serverless collection {collection_id}")
        if not self.execute:
            return

        try:
            self.aoss.delete_collection(id=collection_id)
        except ClientError as error:
            if self.is_missing(error):
                print("  OpenSearch collection already absent")
            else:
                raise

    def empty_and_delete_bucket(self, bucket: str) -> None:
        self.announce(f"All objects and S3 bucket {bucket}")
        if not self.execute:
            return

        try:
            paginator = self.s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                objects = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for item in (
                        page.get("Versions", [])
                        + page.get("DeleteMarkers", [])
                    )
                ]
                for start in range(0, len(objects), 1000):
                    self.s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": objects[start : start + 1000]},
                    )

            # Handles an unversioned bucket and any objects not returned above.
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                objects = [
                    {"Key": item["Key"]}
                    for item in page.get("Contents", [])
                ]
                for start in range(0, len(objects), 1000):
                    self.s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": objects[start : start + 1000]},
                    )

            self.s3.delete_bucket(Bucket=bucket)
        except ClientError as error:
            if self.is_missing(error):
                print("  S3 bucket already absent")
            else:
                raise

    def delete_api(self, resources: Resources) -> None:
        self.announce(f"API Gateway REST API {resources.rest_api_id}")
        if not self.execute:
            return

        try:
            self.apigateway.delete_rest_api(
                restApiId=resources.rest_api_id
            )
        except ClientError as error:
            if self.is_missing(error):
                print("  REST API already absent")
            else:
                raise

    def delete_lambdas(self, resources: Resources) -> None:
        for function_name in resources.lambda_functions:
            self.announce(f"Lambda function {function_name}")
            if not self.execute:
                continue
            try:
                self.lambda_client.delete_function(
                    FunctionName=function_name
                )
            except ClientError as error:
                if self.is_missing(error):
                    print(f"  Lambda {function_name} already absent")
                else:
                    raise

    def run(self, resources: Resources) -> None:
        self.destroy_runtime(resources)
        self.delete_gateway(resources)
        self.delete_memory(resources)
        collection_id = self.delete_knowledge_base(resources)
        self.delete_collection(collection_id)
        self.empty_and_delete_bucket(resources.s3_bucket)
        self.delete_api(resources)
        self.delete_lambdas(resources)
        self.announce(
            "Additional Gateway, KB, Lambda, and OpenSearch IAM roles were "
            "not explicitly deleted"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean up AWS resources for the AgentCore course project."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform deletions. Without this option, only print the plan.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --execute: DELETE-{EXPECTED_ACCOUNT_ID}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resources = Resources()

    if args.execute and args.confirm != f"DELETE-{EXPECTED_ACCOUNT_ID}":
        print(
            "Refusing destructive cleanup. Supply "
            f"--confirm DELETE-{EXPECTED_ACCOUNT_ID}.",
            file=sys.stderr,
        )
        return 2

    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
    except ClientError as error:
        print(f"Unable to verify AWS credentials: {error}", file=sys.stderr)
        return 1

    if account_id != EXPECTED_ACCOUNT_ID:
        print(
            f"Refusing cleanup: authenticated account is {account_id}, "
            f"expected {EXPECTED_ACCOUNT_ID}.",
            file=sys.stderr,
        )
        return 2

    print(f"Verified AWS account {account_id} in {args.region}.")
    Cleanup(args.region, args.execute).run(resources)
    if not args.execute:
        print(
            "\nDry run only. To delete these resources, run:\n"
            f"python cleanup_aws.py --execute "
            f"--confirm DELETE-{EXPECTED_ACCOUNT_ID}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
