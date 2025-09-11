#!/bin/bash
set -e

# 변수 설정
AWS_REGION="us-east-2"      # 사용할 리전 (예: 서울 리전)
REPO_NAME="bridge"          # 생성할 ECR 리포지토리 이름
IMAGE_TAG="1.0.0"                  # 푸시할 이미지 태그


# 2. ECR 로그인
echo "🔑 ECR 로그인 중..."
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin $(aws sts get-caller-identity --query "Account" --output text).dkr.ecr.${AWS_REGION}.amazonaws.com

# 3. Docker 이미지 빌드
ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:${IMAGE_TAG}"

echo "🐳 Docker 이미지 빌드 중..."
docker build -t ${REPO_NAME}:${IMAGE_TAG} .

# 4. ECR용 태그 붙이기
echo "🏷️ ECR 태그 붙이는 중..."
docker tag ${REPO_NAME}:${IMAGE_TAG} ${ECR_URI}

# 5. ECR에 푸시
echo "🚀 ECR에 이미지 푸시 중..."
docker push ${ECR_URI}

echo "🎉 완료! ECR 이미지 URI: ${ECR_URI}"