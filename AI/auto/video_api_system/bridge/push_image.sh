#!/bin/bash
set -e

# 변수 설정
AWS_REGION="us-east-2"     # 사용할 리전 (예: 서울 리전)
REPO_NAME="create-video"          # 생성할 ECR 리포지토리 이름

# 1. ECR 리포지토리 존재 여부 확인 후 생성
echo "🔍 ECR 리포지토리 확인 중..."
if ! aws ecr describe-repositories --repository-names ${REPO_NAME} --region ${AWS_REGION} >/dev/null 2>&1; then
  echo "📦 리포지토리가 없으므로 생성합니다: ${REPO_NAME}"
  aws ecr create-repository --repository-name ${REPO_NAME} --region ${AWS_REGION}
else
  echo "✅ 리포지토리 이미 존재합니다: ${REPO_NAME}"
fi