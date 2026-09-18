import { RemovalPolicy } from 'aws-cdk-lib';
import { Certificate, CertificateValidation } from 'aws-cdk-lib/aws-certificatemanager';
import {
  Distribution,
  Function as CloudFrontFunction,
  FunctionCode,
  FunctionEventType,
  FunctionRuntime,
  ViewerProtocolPolicy,
} from 'aws-cdk-lib/aws-cloudfront';
import { HttpOrigin } from 'aws-cdk-lib/aws-cloudfront-origins';
import { ARecord, AaaaRecord, IHostedZone, RecordTarget } from 'aws-cdk-lib/aws-route53';
import { CloudFrontTarget } from 'aws-cdk-lib/aws-route53-targets';
import { Construct } from 'constructs';
import { QuickRedirectConfig, ResourceName, createConstructId, createResourceName } from '../common/config';

/**
 * Configuration for {@link RedirectDistribution}.
 */
export interface RedirectDistributionProps {
  /** Validated deployer configuration. */
  readonly config: QuickRedirectConfig;
  /** Hosted zone that owns the domain, used for cert validation and alias records. */
  readonly hostedZone: IHostedZone;
  /** Removal policy applied to every resource in the group. */
  readonly removalPolicy: RemovalPolicy;
}

/**
 * Edge-only redirect: an ACM certificate, a viewer-request CloudFront Function
 * that returns an unconditional 301, a CloudFront distribution wired to that
 * function, and Route 53 A/AAAA alias records. Composes the complete redirect
 * as one logical unit; the stack supplies the hosted zone and config.
 */
export class RedirectDistribution extends Construct {
  /** The CloudFront distribution the DNS alias records point at. */
  public readonly distribution: Distribution;

  constructor(scope: Construct, id: string, props: RedirectDistributionProps) {
    super(scope, id);

    const { config, hostedZone, removalPolicy } = props;
    const { domainName, redirectTargetUrl } = config;

    const certificate = new Certificate(this, createConstructId('Certificate'), {
      domainName,
      validation: CertificateValidation.fromDns(hostedZone),
    });
    certificate.applyRemovalPolicy(removalPolicy);

    const redirectFunction = new CloudFrontFunction(this, createConstructId('RedirectFunction'), {
      functionName: createResourceName(config.projectName, ResourceName.REDIRECT_FUNCTION),
      runtime: FunctionRuntime.JS_2_0,
      code: FunctionCode.fromInline(this.buildRedirectCode(redirectTargetUrl)),
    });
    redirectFunction.applyRemovalPolicy(removalPolicy);

    this.distribution = new Distribution(this, createConstructId('Distribution'), {
      domainNames: [domainName],
      certificate,
      defaultBehavior: {
        // CloudFront requires a syntactic origin, but the viewer-request
        // function short-circuits every request with a 301 before any origin
        // fetch, so this placeholder host is never contacted.
        origin: new HttpOrigin('placeholder.invalid'),
        viewerProtocolPolicy: ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        functionAssociations: [
          {
            function: redirectFunction,
            eventType: FunctionEventType.VIEWER_REQUEST,
          },
        ],
      },
    });
    this.distribution.applyRemovalPolicy(removalPolicy);

    const aliasTarget = RecordTarget.fromAlias(new CloudFrontTarget(this.distribution));

    const ipv4Record = new ARecord(this, createConstructId('AliasRecordIpv4'), {
      zone: hostedZone,
      recordName: domainName,
      target: aliasTarget,
    });
    ipv4Record.applyRemovalPolicy(removalPolicy);

    const ipv6Record = new AaaaRecord(this, createConstructId('AliasRecordIpv6'), {
      zone: hostedZone,
      recordName: domainName,
      target: aliasTarget,
    });
    ipv6Record.applyRemovalPolicy(removalPolicy);
  }

  /**
   * Generates the inline CloudFront Function source that unconditionally returns
   * a 301 to the redirect target. The target is embedded as a JSON string
   * literal so any characters in the URL are safely escaped.
   *
   * @param redirectTargetUrl - Absolute https URL to redirect to.
   * @returns `cloudfront-js-2.0` handler source.
   */
  private buildRedirectCode(redirectTargetUrl: string): string {
    return [
      'function handler(event) {',
      '  return {',
      '    statusCode: 301,',
      "    statusDescription: 'Moved Permanently',",
      '    headers: {',
      `      location: { value: ${JSON.stringify(redirectTargetUrl)} },`,
      "      'cache-control': { value: 'max-age=3600' }",
      '    }',
      '  };',
      '}',
    ].join('\n');
  }
}
