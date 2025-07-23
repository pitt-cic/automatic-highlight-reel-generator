import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as secretsmanager from 'aws-cdk-lib/aws-secrets-manager';
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

    // Create Auto Scaling Group for GPU instancesy
    const autoScalingGroup = new autoscaling.AutoScalingGroup(this, 'VideoProcessorASG', {
      vpc,
      // Switched to a GPU-enabled instance type for ML workloads
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE),
      // Use the ECS-optimized AMI with GPU support
      machineImage: ecs.EcsOptimizedImage.amazonLinux2(ecs.AmiHardwareType.GPU),
      minCapacity: 0, // Can scale to 0 to save costs when idle
      maxCapacity: 2,
      securityGroup,
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
    }); // Logs for ECS tasks are retained for one week and then deleted

    // Create Task Role
    const taskRole = new iam.Role(this, 'VideoProcessorTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      // The managed policy is removed in favor of more specific grants below
      // which grant read from 'videos/*' and write to 'results/*'
    });

    // Create Execution Role
    const executionRole = new iam.Role(this, 'VideoProcessorExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ], // Provides necessary permissions for ECS task execution
    });

    // Create Task Definition
    const taskDefinition = new ecs.Ec2TaskDefinition(this, 'VideoProcessorTaskDef', {
      family: 'video-processor',
      taskRole,
      executionRole,
      networkMode: ecs.NetworkMode.AWS_VPC,
    });

    // Reference the secret from AWS Secrets Manager to pass to the Docker build
    const huggingFaceTokenSecret = secretsmanager.Secret.fromSecretNameV2(this, 'HuggingFaceTokenSecret', 'hugging_face_token');

    // Add Container to Task Definition
    const container = taskDefinition.addContainer('video-processor', {
      image: ecs.ContainerImage.fromAsset('./video-processing', {
        // This securely passes the secret to the Docker build at deploy time
        // The key 'huggingface_token' must match the 'id' in the Dockerfile RUN command
        buildSecrets: {
          huggingface_token: ecr_assets.DockerBuildSecret.fromSecretsManager(huggingFaceTokenSecret, 'HUGGINGFACE_TOKEN'),
        },
        // It's good practice to specify the platform
        platform: ecr_assets.Platform.LINUX_AMD64,
      }),
      // Increased resources for a GPU-intensive ML task
      memoryLimitMiB: 15360, // ~15GB, suitable for g4dn.xlarge (16GiB total)
      cpu: 4096,             // 4 vCPUs for g4dn.xlarge
      gpuCpus: 1,            // Request 1 GPU from the instance
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'video-processor',
        logGroup,
      }),
      essential: true, // Marks this container as essential for the task
    });

    // Create S3 Bucket
    const videoBucket = new s3.Bucket(this, 'VideoBucket', {
      bucketName: `video-uploads-${this.account}-${this.region}-${this.stackName}`.toLowerCase(),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    }); // S3 bucket to store video uploads

    // Grant specific S3 permissions to the task role
    // Allow reading from the 'videos/' prefix
    videoBucket.grantRead(taskRole, 'videos/*');
    // Allow writing to the 'results/' prefix
    videoBucket.grantWrite(taskRole, 'results/*');

    // Create a dedicated Log Group for the Lambda for easier debugging
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
      },
    });

    // Grant Lambda permissions to run the specific ECS task
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecs:RunTask'],
      resources: [taskDefinition.taskDefinitionArn],
    }));

    // Grant Lambda permission to pass the task and execution roles to ECS
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['iam:PassRole'],
      resources: [taskRole.roleArn, executionRole.roleArn],
    }));

    // Add S3 notification to trigger Lambda
    videoBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(triggerLambda), // Lambda function to be triggered
      { prefix: 'videos/' }
    );

    // Explicitly grant S3 permission to invoke the Lambda.
    // Note: s3n.LambdaDestination should do this automatically, but adding it
    // explicitly can resolve potential misconfigurations.
    triggerLambda.addPermission('S3InvokePermission', {
      principal: new iam.ServicePrincipal('s3.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: videoBucket.bucketArn,
      sourceAccount: this.account,
    });

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
      value: triggerLambda.logGroup.logGroupName,
      description: 'CloudWatch log group for the trigger Lambda function',
    });
    
  }
}