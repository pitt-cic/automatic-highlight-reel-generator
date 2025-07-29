# Automatic Highlight Reel Generator

| Index | Description |
|:----------------|:-----------|
| [Overview](#overview) | See the motivation behind this project. |
| [Description](#description) | Learn more about the problem, the implemented solution, and the technologies used. |
| [Deployment Guide](#deployment) | How to install and deploy the highlight generator. |
| [How to Use](#how-to-use) | Instructions to use the highlight generator. |
| [Algorithm](#algorithm) | An explanation of the three-stage video processing pipeline. |
| [Lessons Learned](#lessons-learned) | Limitations and lessons learned. |
| [Performance and Cost](#performance-and-cost) | A guide to performance and costs for different instances. |
| [Credits](#credits) | Meet the team behind this project. |
| [License](#license) | License details. |

---

## Overview

The Automatic Highlight Reel Generator is a computer vision application that uses modern AI techniques to analyze long video recordings of sporting events and automatically extract key moments. This project originated from a challenge presented by the University of Pittsburgh’s diving team. Coaches and athletes were spending hours manually reviewing practice footage where the actual dives constituted only about 20% of the recording. To address this, we developed a solution that provides immense value by automating this tedious process, allowing for more efficient and focused review of important repetitions.

While initially designed for diving, our solution uses a powerful Vision Language Model (VLM) that can detect events based on natural language prompts, making it adaptable to a wide variety of sports and actions.

---

## Description

This project aims to address the challenge of manually reviewing lengthy video footage by creating an automated, event-driven pipeline on AWS. When a user uploads a video, a process is triggered that analyzes the footage and identifies frames matching a specific prompt (e.g., “Is there a person in the air jumping into the water?”). The timestamps of these positively identified frames are then grouped together to create distinct time intervals for each event. These intervals are used to clip the original video, and the resulting clips are merged to generate a concise highlight reel.

### Problem

Coaches, athletes, and staff in many sports record long practice or game sessions for later review. These recordings can often exceed an hour, with the actual relevant action making up only a small percentage of the total runtime. The manual effort required to find and clip these key moments is time-consuming and inefficient. In the case of Pitt’s diving team, training sessions were being recorded and displayed on a 30-second delay for immediate, in-practice feedback. However, this footage was then discarded, preventing any further comprehensive review or long-term analysis by coaches and athletes. An automated system can reduce review time significantly, allowing for more focused and productive analysis.

### Initial Challenges

- **Event Boundary Detection:** A key challenge was determining the precise start and end of a dynamic action, like a dive, from a single frame where the event was detected. We needed a reliable way to create a clip that captured the entire motion.
- **Model Optimization:** For this application, missing a key event (a false negative) is far more detrimental than incorrectly identifying a non-event (a false positive). Therefore, we had to prioritize the model's recall to ensure no important repetitions were lost.
- **Extreme Environmental Variability:** The solution needed to be robust enough to function across different venues and camera setups. In some cases, key elements like the 10-meter diving platform were completely out of frame, meaning the only visible action was the diver already in mid-air. This made it impossible to rely on detecting the athlete's initial jump.

### Our Approach

- **Falling Motion Detection:** Because the initial jump was often invisible, we couldn't use complex methods like pose estimation to detect a "leaping" action. Our approach therefore focused on detecting the most reliable and consistently visible part of the event: the downward motion of the diver falling toward the water.
- **Leveraging Pre-trained Models:** To ensure robustness and enable rapid iteration, we opted to use powerful, pre-trained Vision-Language Models (VLMs). This strategy bypassed the time-intensive process of data collection, labeling, and custom model training, preventing overfitting and providing a foundation that could be generalized to other sports.
- **Dynamic Interval Creation:** We developed a dynamic pipeline to define event boundaries from the model's raw output. This process first filters the frame-by-frame predictions by a confidence threshold to retain only high-certainty detections. These positive frames are clustered based on their timestamps to isolate distinct events and filter out sporadic, noisy predictions. Finally, a continuous time interval is generated around each cluster—with a small buffer added to the start and end—to ensure the entire action is captured.

### Architecture Diagram

![HighlightProcessorDiagram2.png](/public/HighlightProcessorDiagram.png)

### Functionality

Our project utilizes AWS to implement an event-driven pipeline that automatically runs a containerized processing job on Amazon ECS when a file is uploaded to an S3 bucket.

The end-to-end workflow is designed as follows:

- **Trigger**: A user uploads a video file to a designated `videos/` prefix in an S3 bucket.
- **Orchestration**: The S3 upload event triggers an AWS Lambda function.
- **Task Execution**: The Lambda function launches a task on an Amazon ECS cluster using the EC2 launch type. This task runs a Docker container on a GPU-enabled EC2 instance to perform the video analysis.
- **Processing**: Inside the container, a Python script orchestrates a multi-stage process to downsample the video, run inference with the Pali-Gemma model to find events, and create clips.
- **Output**: The final highlight reel is uploaded to a `results/` prefix in the same S3 bucket.

### Technologies

**Amazon Web Services:**

- Amazon S3
- AWS Lambda
- Amazon ECS
- Amazon EC2 (for GPU-enabled instances)
- Amazon SageMaker (for rapid prototyping and model testing)
- AWS CDK (for infrastructure)

**Software and Machine Learning:**

- Python
- Docker
- Hugging Face (for model hosting)
- Pali-Gemma-2
- PyTorch
- FFmpeg
---
## Deployment

This section provides a complete guide for deploying the Automatic Highlight Reel Generator on AWS.

### Prerequisites

Before deploying this solution, ensure you have:

1. **AWS Account**: An active AWS account with appropriate permissions to create resources
2. **AWS CLI**: Installed and configured with credentials (`aws configure`)
3. **Node.js and npm**: Version 14.x or higher for CDK
4. **AWS CDK**: Install globally with `npm install -g aws-cdk`
5. **Docker**: Installed and running on your local machine
6. **Hugging Face Account**: Required to access the Pali-Gemma model
   - Create an account at [Hugging Face](https://huggingface.co)
   - Generate an access token from your account settings
   - Accept the Pali-Gemma model license agreement

### Step 1: Clone and Setup the Project

```bash
# Clone the repository
git clone 
cd highlight-processor

# Install CDK dependencies
npm install

# Build the TypeScript CDK code
npm run build
```

### Step 2: Configure Environment Variables

```bash
HUGGINGFACE_TOKEN='your_huggingface_token_here'
```

### Step 3: Build the Docker Container

The container includes all dependencies and the pre-downloaded Pali-Gemma model:

```bash
cd video-processing

# Build the Docker image with your Hugging Face token
docker build --build-arg HUGGINGFACE_TOKEN=$HUGGINGFACE_TOKEN -t highlight-processor .

# This process will:
# - Install CUDA libraries and FFmpeg
# - Install all Python dependencies
# - Download and cache the Pali-Gemma model (~6GB)
# Note: This may take 15-30 minutes on first build

cd ..
```

### Step 4: Bootstrap CDK (First-time only)

If this is your first time using CDK in this AWS account/region:

```bash
cdk bootstrap aws://$AWS_ACCOUNT_ID/$AWS_DEFAULT_REGION
```

### Step 5: Deploy the Infrastructure

```bash
# Preview the resources that will be created
cdk diff

# Deploy the stack
cdk deploy

# You'll be prompted to approve security-related changes
# Type 'y' to proceed
```

The deployment will create:
- VPC with public/private subnets
- ECS Cluster with EC2 capacity (g4dn.2xlarge GPU instances)
- S3 bucket for video storage
- Lambda function for orchestration
- IAM roles and policies
- CloudWatch log groups

### Step 6: Note the Outputs

After successful deployment, CDK will display outputs similar to:

```
Outputs:
HighlightProcessorStack.VideoBucketName = highlight-processor-videos-xxxxx
HighlightProcessorStack.LogGroupName = /ecs/video-processor
HighlightProcessorStack.ClusterName = HighlightProcessorCluster
```

Save these values, especially the **VideoBucketName**, as you'll need them for usage.

### Configuration Options

You can customize the deployment by modifying environment variables in the CDK stack:

- **``EVENT_PROMPT``**: Change the detection prompt (default: "Is there a person in the air jumping into the water?")
- **Instance Type**: Modify from g4dn.2xlarge to other GPU instances in `highlight-processor-stack.ts`
- **Confidence Threshold**: Adjust detection sensitivity in the container environment variables

---

## How to Use

### Basic Usage

1. **Upload a Video**

   Upload your video file to the S3 bucket created during deployment:

   ```bash
   # Using AWS CLI
   aws s3 cp your-video.mp4 s3://<VideoBucketName>/videos/
   
   # Example
   aws s3 cp practice-session-2024.mp4 s3://highlight-processor-videos-xxxxx/videos/
   ```

   Supported formats: MP4, MOV, AVI (any format supported by FFmpeg)

2. **Monitor Processing**

   The pipeline triggers automatically upon upload. Monitor progress in CloudWatch:

   ```bash
   # Watch logs in real-time
   aws logs tail /ecs/video-processor --follow
   
   # You'll see logs for each stage:
   # - "=== Video Highlight Processor Starting ==="
   # - "Stage 1: Downsampling video..."
   # - "Stage 2: Running inference..."
   # - "Stage 3: Creating clips..."
   # - "=== Video Highlight Processor Finished Successfully ==="
   ```

3. **Download Results**

   ```bash
   # List results
   aws s3 ls s3://<VideoBucketName>/results/
   
   # Download the highlight reel
   aws s3 cp s3://<VideoBucketName>/results/your-video_merged_clips.mp4 ./
   ```

### Custom Event Prompts

To detect different events update, the EVENT_PROMPT environment variable:

```typescript
// In highlight-processor-stack.ts
environment: {
    EVENT_PROMPT: 'Is there a person performing a backflip?',
    // ... other variables
}
```

Example prompts for other sports:
- Basketball: "Is there a person shooting a basketball?"
- Gymnastics: "Is there a person performing on the balance beam?"
- Soccer: "Is there a goal being scored?"

### Monitoring and Troubleshooting

#### Check Task Status

```bash
# List recent ECS tasks
aws ecs list-tasks --cluster HighlightProcessorCluster

# Describe a specific task
aws ecs describe-tasks --cluster HighlightProcessorCluster --tasks <task-arn>
```

#### Common Issues and Solutions

1. **Task fails to start**
   - Check ECS cluster has available GPU instances
   - Verify Docker image was built correctly
   - Ensure sufficient IAM permissions

2. **No output generated**
   - Check CloudWatch logs for errors
   - Verify the video format is supported
   - Ensure S3 permissions allow PutObject to results/

3. **Poor detection quality**
   - Adjust confidence threshold in the code
   - Try different prompts that better describe your event
   - Ensure video quality is sufficient (minimum 720p recommended)

4. **Processing takes too long**
   - Longer videos naturally take more time
   - Consider using larger GPU instances 
   - Check if ECS tasks are queuing due to capacity

### Cleanup

To remove all resources and avoid ongoing charges:

```bash
# Empty the S3 bucket first (WARNING: This deletes all videos)
aws s3 rm s3://<VideoBucketName> --recursive

# Destroy the CDK stack
cdk destroy

# Confirm deletion when prompted
```
---

## Algorithm

The core of this project is a three-stage pipeline orchestrated by a monolithic Python script running inside a Docker container.

### Stage 1: Downsampling and Timestamp Generation

To make the analysis efficient, the original high-resolution video is first downsampled to a lower frame rate (e.g., 4 FPS). A critical step here is the creation of a timestamp mapping file (CSV), which links every frame of the downsampled video back to the precise timestamp in the original video. This ensures the final clips are cut from the high-quality source.

### Stage 2: Event Detection and Interval Creation

This is the main event detection stage.

1. **Run Inference**: The downsampled video is processed frame-by-frame using the **Pali-Gemma** vision-language model. For each frame, the model is given a prompt (e.g., “Is there a person in the air jumping into the water?”) and returns a “yes” or “no” answer with a confidence score.
2. **Post-Process**: The raw predictions are filtered to remove low-confidence and isolated detections. Consecutive “yes” frames are then grouped into events. A time buffer is added to each event to ensure the full action is captured, and any overlapping intervals are merged.
3. **Save Intervals**: The final output is a CSV file containing the `start` and `end` timestamps for each highlight-worthy event.

### Stage 3: Clipping and Merging

In the final stage, the system uses the predicted intervals from Stage 2 to create the highlight reel.

1. **Extract Clips**: Using the `ffmpeg` library, the script iterates through the intervals CSV. For each `start` and `end` time, it extracts that exact segment from the **original, high-resolution video**.
2. **Merge Clips**: After all individual clips are extracted, `ffmpeg` is used again to concatenate them in chronological order into a single, seamless video file.
3. **Upload Final Video**: This final merged video is the end product, which is then uploaded to S3.

---

## Lessons Learned

> To be updated

---

## Performance and Cost
This section provides cost and performance estimates for deploying and running the Automatic Highlight Reel Generator on AWS. 

*Note: These figures are estimates. Actual costs and processing times may vary based on video characteristics, system load, and AWS pricing changes.*

### AWS EC2 Instance: g4dn.2xlarge

* **Instance Type:** g4dn.2xlarge
* **vCPUs:** 8
* **Memory:** 32 GiB
* **GPU:** 1x NVIDIA T4
* **On-Demand Price (us-east-1):** Approximately $0.752 per hour
* ``BATCH_SIZE = 16``  

| Video Length (Original) | Processing Time (Total) | Real-Time Speed             | Inference Speed (Avg. FPS) | Estimated Cost |
|--------------------------|--------------------------|------------------------------|-----------------------------|----------------|
| ~1 minute                | ~33 seconds              | ~1.3× faster than real-time  | ~10.14 FPS                  | < $0.01        |
| ~15 minutes              | ~10 minutes              | ~1.5× faster than real-time  | ~8.98 FPS                   | ~ $0.13        |
| ~2 hours, 6 minutes      | ~90 minutes              | ~1.4× faster than real-time  | ~8.94 FPS                   | ~ $1.13        |

*Note: "Real-Time Speed" compares the total processing time to the original video’s length (a value greater than 1.0× is faster than real-time). "Inference Speed" measures how quickly the model processes the downsampled video (at 4 FPS).*


> To be updated with performance and cost data for other GPU-enabled EC2 instance types. 



---

## Credits
**automatic-highlight-reel-generator** is an open source software. The following people have contributed to this project.

**Developers:**  
- [Roman Koshovnyk](https://www.linkedin.com/in/roman-koshovnyk-452971161/)
- [Rowan Morse](https://www.linkedin.com/in/rowan-morse/)

This project is designed and developed with guidance and support from the **University of Pittsburgh Cloud Innovation Center** and **Amazon Web Services (AWS)**.

---

## License

> To be updated

This project will be distributed under an open-source license.
