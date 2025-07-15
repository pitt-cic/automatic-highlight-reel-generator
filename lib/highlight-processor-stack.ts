import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
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
      clusterName: 'video-processor-cluster',
    });

    // Create Auto Scaling Group for GPU instancesy
    const autoScalingGroup = new autoscaling.AutoScalingGroup(this, 'VideoProcessorASG', {
          vpc,
          instanceType: new ec2.InstanceType('t3.medium'),
          machineImage: ecs.EcsOptimizedImage.amazonLinux2(ecs.AmiHardwareType.STANDARD), // Standard AMI
          minCapacity: 0,
          maxCapacity: 2,
          desiredCapacity: 1,
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
      logGroupName: '/ecs/video-processor',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    }); // Logs for ECS tasks are retained for one week and then deleted

    // Create Task Role
    const taskRole = new iam.Role(this, 'VideoProcessorTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonS3ReadOnlyAccess'),
      ], // Allows the task to read from S3
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

    // Add Container to Task Definition
    const container = taskDefinition.addContainer('video-processor', {
      image: ecs.ContainerImage.fromAsset('./video-processing'),
      memoryLimitMiB: 512, // Reduced for t3.medium instance
      cpu: 256,             // Reduced for t3.medium instance
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'video-processor',
        logGroup,
      }),
      essential: true, // Marks this container as essential for the task
    });

    // Create S3 Bucket
    const videoBucket = new s3.Bucket(this, 'VideoBucket', {
      bucketName: `video-uploads-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    }); // S3 bucket to store video uploads

    // Grant bucket access to task role
    videoBucket.grantRead(taskRole);

    // Create Lambda Function
    const triggerLambda = new lambda.Function(this, 'VideoTriggerLambda', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset('./lambda'),
      timeout: cdk.Duration.minutes(5),
      environment: {
        CLUSTER_NAME: cluster.clusterName,
        TASK_DEFINITION: taskDefinition.taskDefinitionArn,
        SUBNET_IDS: vpc.privateSubnets.map(subnet => subnet.subnetId).join(','),
        SECURITY_GROUP: securityGroup.securityGroupId,
        ASSIGN_PUBLIC_IP: 'DISABLED',
      },
    });

    // Grant Lambda permissions to run ECS tasks
    triggerLambda.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'ecs:RunTask',
        'ecs:DescribeTasks',
        'iam:PassRole',
      ],
      resources: ['*'],
    }));

    // Add S3 notification to trigger Lambda
    videoBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(triggerLambda), // Lambda function to be triggered
      { prefix: 'videos/' }
    );

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
  }
}