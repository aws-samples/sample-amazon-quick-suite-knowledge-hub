import { RemovalPolicy } from 'aws-cdk-lib';

export enum ProjectName {
  QUICK_SP_EXPORT = 'QuickSpExport',
}

export enum ResourceName {
  EXPORT_BUCKET = 'ExportBucket',
}

export interface SharePointExportConfig {
  readonly projectName: ProjectName;
  readonly retainResources: boolean;
  readonly enableXRay: boolean;
  readonly entraTenantId: string;
  readonly entraClientId: string;
}

export const createResourceName = (
  projectName: ProjectName,
  resourceName: string,
): string =>
  `${projectName}${resourceName.charAt(0).toUpperCase()}${resourceName.slice(1)}`;

export const createBucketName = (
  projectName: ProjectName,
  bucketName: string,
  account: string,
  region: string,
): string => {
  const kebab = projectName
    .replace(/([A-Z])/g, '-$1')
    .toLowerCase()
    .replace(/^-/, '');
  return `${kebab}-${bucketName}-${account}-${region}`;
};

export const createStackName = (
  projectName: ProjectName,
  stackName: string,
): string =>
  `${projectName}${stackName.charAt(0).toUpperCase()}${stackName.slice(1)}Stack`;

export const createConstructId = (resourceName: string): string =>
  resourceName.charAt(0).toUpperCase() + resourceName.slice(1);

export const getRemovalPolicy = (retain: boolean): RemovalPolicy =>
  retain ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;
