#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
export declare class QuickChatEmbeddingStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props?: cdk.StackProps);
    private addNagSuppressions;
    private generateEnvFile;
}
