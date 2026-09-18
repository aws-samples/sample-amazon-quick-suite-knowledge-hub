import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import { HostedZone } from 'aws-cdk-lib/aws-route53';
import { Construct } from 'constructs';
import { QuickRedirectConfig, createConstructId, getRemovalPolicy } from '../common/config';
import { RedirectDistribution } from '../construct-groups/redirect-distribution';

/**
 * Props for {@link QuickRedirectStack}.
 */
export interface QuickRedirectStackProps extends StackProps {
  /** Validated deployer configuration. */
  readonly config: QuickRedirectConfig;
}

/**
 * Thin orchestrator: looks up the hosted zone, composes the redirect construct
 * group, and outputs the distribution domain name and redirect target.
 */
export class QuickRedirectStack extends Stack {
  constructor(scope: Construct, id: string, props: QuickRedirectStackProps) {
    super(scope, id, props);

    const { config } = props;

    const removalPolicy = getRemovalPolicy(config.retainResources);

    const hostedZone = HostedZone.fromLookup(this, createConstructId('HostedZone'), {
      domainName: config.hostedZoneDomain,
    });

    const redirect = new RedirectDistribution(this, createConstructId('Redirect'), {
      config,
      hostedZone,
      removalPolicy,
    });

    new CfnOutput(this, createConstructId('DistributionDomainName'), {
      value: redirect.distribution.distributionDomainName,
      description: 'CloudFront distribution domain name backing the custom domain.',
    });

    new CfnOutput(this, createConstructId('RedirectTargetUrl'), {
      value: config.redirectTargetUrl,
      description: 'URL that the custom domain 301-redirects visitors to.',
    });
  }
}
