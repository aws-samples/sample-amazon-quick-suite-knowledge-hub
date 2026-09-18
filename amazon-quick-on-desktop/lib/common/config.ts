import { RemovalPolicy } from 'aws-cdk-lib';

export enum ProjectName {
  QUICK_DESKTOP = 'QuickDesktop',
}

export enum ResourceName {
  USER_POOL = 'UserPool',
  APP_CLIENT = 'AppClient',
  AUTH_PROXY_API = 'AuthProxy',
  AUTH_PROXY_FUNCTION = 'AuthProxyFunction',
}

export enum CognitoDomainPrefix {
  DEFAULT = 'quick-desktop',
}

export interface QuickDesktopConfig {
  readonly projectName: ProjectName;
  readonly retainResources: boolean;
  readonly allowedCidrs?: string[];
  readonly mfaRequired?: boolean;
}

export const createResourceName = (
  projectName: ProjectName,
  resourceName: ResourceName,
): string => `${projectName}${resourceName}`;

export const createDomainPrefix = (
  domainPrefix: CognitoDomainPrefix,
  account: string,
  region: string,
): string => `${domainPrefix}-${account}-${region}`;

export const createConstructId = (resourceName: string): string =>
  resourceName.charAt(0).toUpperCase() + resourceName.slice(1);

export const createStackName = (
  projectName: ProjectName,
  stackName: string,
): string => {
  const pascal = stackName.charAt(0).toUpperCase() + stackName.slice(1);
  return `${projectName}${pascal}Stack`;
};

export const getRemovalPolicy = (retain: boolean): RemovalPolicy =>
  retain ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;
