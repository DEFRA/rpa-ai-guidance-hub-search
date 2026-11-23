#!/bin/bash

# S3 buckets

aws s3 mb --endpoint-url=http://localhost:4566 s3://rpa-ai-guidance-hub-search-store || true

aws s3 mb --endpoint-url=http://localhost:4566 s3://rpa-ai-guidance-hub-managed-docs || true

# SQS queues

# Indexing
aws sqs create-queue --endpoint-url=http://localhost:4566 --queue-name rpa_ai_guidance_hub_search_indexing-deadletter || true
search_indexing_deadletter_arn=$(aws sqs get-queue-attributes --endpoint-url=http://localhost:4566 \
  --queue-url http://localhost:4566/000000000000/rpa_ai_guidance_hub_search_indexing-deadletter \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sqs create-queue --endpoint-url=http://localhost:4566 --queue-name rpa_ai_guidance_hub_search_indexing \
  --attributes "{\"VisibilityTimeout\":\"60\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${search_indexing_deadletter_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}" || true

# Index sync
aws sqs create-queue --endpoint-url=http://localhost:4566 --queue-name rpa_ai_guidance_hub_search_index_sync-deadletter.fifo \
  --attributes '{"FifoQueue":"true"}' || true
search_index_sync_deadletter_arn=$(aws sqs get-queue-attributes --endpoint-url=http://localhost:4566 \
  --queue-url http://localhost:4566/000000000000/rpa_ai_guidance_hub_search_index_sync-deadletter.fifo \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sqs create-queue --endpoint-url=http://localhost:4566 --queue-name rpa_ai_guidance_hub_search_index_sync.fifo \
  --attributes "{\"FifoQueue\":\"true\",\"ContentBasedDeduplication\":\"false\",\"DeduplicationScope\":\"queue\",\"FifoThroughputLimit\":\"perQueue\",\"VisibilityTimeout\":\"60\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${search_index_sync_deadletter_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}" || true

# SNS topics

guide_events_topic_arn=$(aws sns create-topic --endpoint-url=http://localhost:4566 \
  --name rpa_ai_guidance_hub_guide_events --query TopicArn --output text)

# SNS subscriptions

search_indexing_arn=$(aws sqs get-queue-attributes --endpoint-url=http://localhost:4566 \
  --queue-url http://localhost:4566/000000000000/rpa_ai_guidance_hub_search_indexing \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sns subscribe --endpoint-url=http://localhost:4566 \
  --topic-arn "${guide_events_topic_arn}" --protocol sqs \
  --notification-endpoint "${search_indexing_arn}" \
  --attributes RawMessageDelivery=true || true
