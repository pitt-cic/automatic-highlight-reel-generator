import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { Construct } from 'constructs';

export class HighlightProcessorStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Create VPC
    const vpc = new ec2.Vpc(this, 'VideoProcessorVPC', {
      maxAzs: 2,
      natGateways: 1,
    });

    // Create Security Group for video processing tasks
    const securityGroup = new ec2.SecurityGroup(this, 'VideoProcessorSG', {
      vpc,
      description: 'Security group for video processor ECS tasks',
      allowAllOutbound: true,
    });

    // Create ECS Cluster
    const cluster = new ecs.Cluster(this, 'VideoProcessorCluster', {
      vpc,
      clusterName: `video-processor-cluster-${this.stackName}`,
    });

    // Create Auto Scaling Group for GPU instances
    const autoScalingGroup = new autoscaling.AutoScalingGroup(this, 'VideoProcessorASG', {
      vpc,
      // GPU-enabled instance type for ML workloads
      // in lib/highlight-processor-stack.ts
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE2),
      machineImage: ecs.EcsOptimizedImage.amazonLinux2023(ecs.AmiHardwareType.GPU),
      minCapacity: 0, // Can scale to 0 to save costs when idle
      maxCapacity: 2,
      securityGroup,
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: autoscaling.BlockDeviceVolume.ebs(100, {  // 100 GB root volume
          deleteOnTermination: true,
          volumeType: autoscaling.EbsDeviceVolumeType.GP3,
        }),
      }],
    });
    
    // Add capacity to cluster
    const capacityProvider = new ecs.AsgCapacityProvider(this, 'VideoProcessorCP', {
      autoScalingGroup,
      enableManagedScaling: true,
      enableManagedTerminationProtection: false,
    });

    cluster.addAsgCapacityProvider(capacityProvider);

    // Create CloudWatch Log Group
    const logGroup = new logs.LogGroup(this, 'VideoProcessorLogs', {
      logGroupName: `/ecs/video-processor-${this.stackName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Task Role
    const taskRole = new iam.Role(this, 'VideoProcessorTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Create Execution Role
    const executionRole = new iam.Role(this, 'VideoProcessorExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Grant the EC2 Instance Role permission to use the Launch Template.
    // This fixes the original "You are not authorized to use launch template" error.
    autoScalingGroup.role.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ['ec2:UseLaunchTemplate'],
        resources: ['*'], // Ideally, scope this down to the specific launch template ARN
      })
    );

    // Create Task Definition
    const taskDefinition = new ecs.Ec2TaskDefinition(this, 'VideoProcessorTaskDef', {
      family: 'video-processor',
      taskRole,
      executionRole,
      networkMode: ecs.NetworkMode.AWS_VPC,
    });

    // Add Container to Task Definition
    taskDefinition.addContainer('video-processor', {
      image: ecs.ContainerImage.fromAsset('./video-processing', {
        platform: Platform.LINUX_AMD64,
        buildArgs: {
          'HUGGINGFACE_TOKEN': process.env.HUGGINGFACE_TOKEN || ''
        }
      }),
      // Resources for GPU-intensive ML task
      memoryLimitMiB: 30720, // ~30GB for g4dn.2xlarge (32GiB total)
      cpu: 8192, // 8 vCPUs for g4dn.2xlarge (1024 CPU units per vCPU)
      gpuCount: 1,           // Request 1 GPU
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'video-processor',
        logGroup,
      }),
      // Set the command to run the main orchestrator
      command: ["python3", "main.py"],
      environment: {
        // Add AWS_REGION for boto3
        AWS_REGION: this.region,
      },
      essential: true,
    });

    // Create S3 Bucket
    const videoBucket = new s3.Bucket(this, 'VideoBucket', {
      bucketName: `video-uploads-${this.account}-${this.region}-${this.stackName}`.toLowerCase(),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Grant specific S3 permissions to the task role
    videoBucket.grantRead(taskRole, 'videos/*');
    videoBucket.grantReadWrite(taskRole, 'results/*');

    // Create a dedicated Log Group for the Lambda
    const triggerLambdaLogGroup = new logs.LogGroup(this, 'TriggerLambdaLogGroup', {
      logGroupName: `/aws/lambda/${this.stackName}-VideoTriggerLambda`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Lambda Function
    const triggerLambda = new lambda.Function(this, 'VideoTriggerLambda', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset('./lambda'),
      logGroup: triggerLambdaLogGroup,
      timeout: cdk.Duration.minutes(5),
      environment: {
        CLUSTER_NAME: cluster.clusterName,
        TASK_DEFINITION: taskDefinition.taskDefinitionArn,
        SUBNET_IDS: vpc.privateSubnets.map(subnet => subnet.subnetId).join(','),
        SECURITY_GROUP: securityGroup.securityGroupId,
        ASSIGN_PUBLIC_IP: 'DISABLED',
        CAPACITY_PROVIDER_NAME: capacityProvider.capacityProviderName,
      },
    });

    // Grant Lambda permissions to run the ECS task
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecs:RunTask'],
      resources: [taskDefinition.taskDefinitionArn],
    }));

    // Grant Lambda permission to read S3 object metadata
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:GetObjectAttributes', 's3:HeadObject'],
      resources: [videoBucket.arnForObjects('videos/*')],
    }));

    // Grant Lambda permission to pass roles to ECS
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['iam:PassRole'],
      resources: [taskRole.roleArn, executionRole.roleArn],
    }));

    // Add S3 notification to trigger Lambda
    videoBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(triggerLambda),
      { prefix: 'videos/' }
    );

    // NOTE: The s3n.LambdaDestination construct automatically adds the necessary 
    // lambda:InvokeFunction permission to the Lambda's policy.
    // The explicit `triggerLambda.addPermission` call is redundant and has been removed.
    
    // Outputs
    new cdk.CfnOutput(this, 'BucketName', {
      value: videoBucket.bucketName,
      description: 'S3 bucket for video uploads',
    });

    new cdk.CfnOutput(this, 'ClusterName', {
      value: cluster.clusterName,
      description: 'ECS cluster name',
    });

    new cdk.CfnOutput(this, 'LogGroupName', {
      value: logGroup.logGroupName,
      description: 'CloudWatch log group for ECS tasks',
    });

    new cdk.CfnOutput(this, 'LambdaLogGroupName', {
      value: triggerLambda.logGroup?.logGroupName || 'No log group',
      description: 'CloudWatch log group for the trigger Lambda function',
    });

    
    // Debug output for troubleshooting
    new cdk.CfnOutput(this, 'TaskDefinitionArn', {
      value: taskDefinition.taskDefinitionArn,
      description: 'Task definition ARN for debugging',
    });
  }
}