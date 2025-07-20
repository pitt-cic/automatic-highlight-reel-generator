#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { HighlightProcessorStack } from '../lib/highlight-processor-stack';

const app = new cdk.App();
new HighlightProcessorStack(app, 'HighlightProcessorStackV2', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});