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
---

## How to Use

### Step 1: Get the S3 Bucket Name

After a successful deployment, the CDK outputs the name of the S3 bucket created for video uploads. You can retrieve this bucket name from the CloudFormation stack outputs.

```bash
# Command to get the bucket name
BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name HighlightProcessorStack --query 'Stacks[0].Outputs[?         OutputKey==`BucketName`].OutputValue' --output text)

# You can then echo the variable to see the bucket name
echo $BUCKET_NAME
```

This will return the bucket name, which will look something like this:
`video-uploads--us-east-1-highlightprocessorstack`

### Step 2: Upload a Video

Upload your video file to the `videos/` prefix in the S3 bucket.

```bash
# Using the BUCKET_NAME variable from the previous step
aws s3 cp your-video.mp4 s3://$BUCKET_NAME/videos/
```

The pipeline supports any video format compatible with FFmpeg, such as MP4, MOV, and AVI.

### Step 3: Monitor the Pipeline

The pipeline is triggered automatically when a new video is uploaded to the S3 bucket. You can monitor the two main stages of the pipeline through CloudWatch Logs:

1.  **Lambda Trigger**: This function is triggered by the S3 upload and starts the ECS task.

    ```bash
    # Tail the logs for the Lambda function
    aws logs tail /aws/lambda/HighlightProcessorStack-VideoTriggerLambda --follow

    # --- Expected Outputs ---
    # The Lambda logs will show the function starting, processing the S3 event,
    # and successfully launching the ECS task.
    [INFO] Lambda triggered. Event: {"Records": [{"s3": {"object": {"key": "videos/your-video.mp4"}}}]}
    [INFO] Processing file: s3://video-uploads--us-east-1-highlightprocessorstack/videos/your-video.mp4
    [INFO] Started ECS task: arn:aws:ecs:us-east-1::task/video-processor-cluster-HighlightProcessorStack/...
    ```

2.  **ECS Task Processing**: This is where the main video processing happens. The log group is named after your CDK stack.

    ```bash
    # Tail the logs for the ECS task
    aws logs tail /ecs/video-processor-HighlightProcessorStack --follow

    # --- Expected Outputs ---
    # The ECS logs show the detailed progress of the video processing pipeline,
    # from downsampling and inference to the final clipping and merging.
    [INFO] === Video Highlight Processor Starting ===
    [INFO] Processing s3://<bucket_name>/videos/your-video.mp4

    [INFO] --- Stage 1 (Downsampling) completed in 3.07s ---
    [INFO] Starting downsampling for '1min_dive' to 4 FPS...
    [INFO] Downsampled video saved to: /tmp/tmphbi7ri49/4fps.mp4

    [INFO] --- Stage 2 (Inference) completed in 25.43s ---
    [INFO] Loading model 'google/paligemma2-3b-mix-224' to device 'cuda'...
    [INFO] Running Inference: 100%|██████████| 171/171 [00:15<00:00, 10.90frame/s]
    [INFO] Saved 3 predicted intervals to: /tmp/tmphbi7ri49/predicted_intervals.csv

    [INFO] --- Stage 3 (Clipping & Merging) completed in 3.55s ---
    [INFO] Extracting clip 1/3: 9.43s to 17.40s -> 1min_dive_clip_001.mp4
    [INFO] Merging 3 clips into highlights.mp4...

    [INFO] Uploading final highlight video to s3://<bucket_name>/results/highlights.mp4
    [INFO] === Video Highlight Processor Finished Successfully in 33.76s ===
    ```



### Step 4: Download the Highlight Reel

Once the processing is complete, the final highlight reel will be available in the `results/` prefix of your S3 bucket.

```bash
# List the results in the bucket
aws s3 ls s3://$BUCKET_NAME/results/

# Download the highlight reel
aws s3 cp s3://$BUCKET_NAME/results/your-video_highlights.mp4 ./
```

### Custom Configuration with `config.yaml`

The `video-processing/config.yaml` file allows for detailed customization of the video processing pipeline. You can modify this file to fine-tune the behavior of each stage.

**Key Configuration Options:**

  * **`main`**:
      * `default_prompt`: Change the default natural language prompt for event detection.
      * `s3_output_prefix`: Specify the S3 folder for the final highlight videos.
  * **`downsampling`**:
      * `target_fps`: Adjust the frames-per-second for faster processing. A lower value (e.g., 2-4) is recommended.
  * **`inference`**:
      * `model_id`: Change the Vision Language Model from Hugging Face.
      * `batch_size`: Adjust the number of frames processed in a single batch to fit your GPU's VRAM.
      * `crop_width_start` and `crop_width_end`: Define a horizontal region of interest to focus the analysis.
  * **`post_processing`**:
      * `confidence_threshold`: Set the minimum confidence score for a "yes" prediction.
      * `grouping_threshold_sec`: Group nearby detections into a single event.
      * `buffer_start_sec` and `buffer_end_sec`: Add extra time to the beginning and end of each clip.
      * `merge_gap_sec`: Merge event intervals that are close to each other.
  * **`clipping`**:
      * `ffmpeg_preset`: Control the trade-off between encoding quality and speed.
      * `crf_value`: Adjust the video quality of the final clips (lower is better quality).
      * `audio_bitrate`: Set the audio bitrate for the final video.

> **Note:** After modifying `config.yaml`, Redeploy the CDK stack for the changes to take effect. **A workaround for this will be added in future commits.**
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

*Note: These figures are estimates based on usage in the `us-east-1` (N. Virginia) region. Actual costs and processing times may vary based on video characteristics, system load, and AWS pricing changes.*

### AWS Service Cost Breakdown

The total cost of this pipeline is a sum of the costs of the individual AWS services used. Here's a breakdown of each component:

| Service | Cost Driver | Estimated Cost |
| :--- | :--- | :--- |
| **Amazon EC2** | `g4dn.2xlarge` instance for processing | ~$0.752 per hour |
| **Amazon S3** | Video storage and requests | ~$0.023 per GB/month |
| **Amazon ECR**| Container image storage | ~$0.10 per GB/month |
| **NAT Gateway** | Hourly fee and data processing | ~$0.045 per hour + ~$0.045 per GB |
| **AWS Secrets Manager** | Storing the Hugging Face token | ~$0.40 per secret/month |
| **Data Transfer**| Outbound to internet (e.g., downloading results) | ~$0.09 per GB |
| **Orchestration & Monitoring** | Lambda, CloudWatch Logs, Metrics, and Alarms | < $0.01 per video (typically within Free Tier) |

---

### Monthly and Per-Video Cost Estimation

You can think of the total cost in two parts: a fixed monthly "floor" cost to keep the service ready, and a variable cost for each video you process.

#### 1. Monthly Floor Cost (Infrastructure)
This is the baseline cost to have the infrastructure deployed and ready.
| Service | Usage | Estimated Monthly Cost |
| :--- | :--- | :--- |
| **Amazon ECR** | ~6 GB container image storage | ~$0.60 |
| **NAT Gateway** | Idle gateway running 24/7 (if not scaled down) | ~$32.40 |
| **Total Estimated Floor Cost**| | **~$33.40/month** |

#### 2. Cost Per Video Run
This is the additional cost incurred each time a video is processed. The example below is for a **2-hour video (~10 GB)**.

| Service | Usage (per video) | Estimated Cost |
| :--- | :--- | :--- |
| **Amazon EC2** | ~1.5 hours of g4dn.2xlarge | ~$1.13 |
| **Amazon S3**| 20 GB storage (input/output) for one month | ~$0.46 |
| **NAT Gateway**| ~1.5 hours active + ~6 GB data processing (model download) | ~$0.34 |
| **Data Transfer** | ~2 GB highlight reel download to the internet | ~$0.18 |
| **Orchestration & Monitoring**| Negligible (within Free Tier for low volume) | ~$0.00 |
| **Total Estimated Cost (per video)**| | **~$2.11** |

---
### AWS EC2 Instance: g4dn.2xlarge

* **Instance Type:** g4dn.2xlarge
* **vCPUs:** 8
* **Memory:** 32 GiB
* **GPU:** 1x NVIDIA T4
* **On-Demand Price (us-east-1):** Approximately $0.752 per hour
* `BATCH_SIZE = 16`

| Video Length (Original) | Processing Time (Total) | Real-Time Speed | Inference Speed (Avg. FPS) | Estimated Cost |
| :--- | :--- | :--- | :--- | :--- |
| ~1 minute | ~33 seconds | ~1.3× faster than real-time | ~10.14 FPS | < $0.01 |
| ~15 minutes | ~10 minutes | ~1.5× faster than real-time | ~8.98 FPS | ~ $0.13 |
| ~2 hours, 6 minutes | ~90 minutes | ~1.4× faster than real-time | ~8.94 FPS | ~ $1.13 |

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
