import { RemovalPolicy } from 'aws-cdk-lib';

/**
 * Logical project name, prefixed onto every named resource for consistency.
 */
export enum ProjectName {
  QUICK_REDIRECT = 'QuickRedirect',
}

/**
 * Static suffixes for named resources. Combined with {@link ProjectName} via
 * {@link createResourceName} to produce collision-free, human-readable names.
 */
export enum ResourceName {
  REDIRECT_FUNCTION = 'Function',
}

/**
 * CDK context keys read once in `lib/app.ts`. Centralised so error messages and
 * the parser reference a single source of truth.
 */
export enum ContextKey {
  DOMAIN_NAME = 'domainName',
  REDIRECT_TARGET_URL = 'redirectTargetUrl',
  HOSTED_ZONE_DOMAIN = 'hostedZoneDomain',
  RETAIN = 'retain',
}

/**
 * Fully validated deployer configuration for the redirect stack. Every field is
 * resolved and safe to consume; optional context keys are defaulted here so
 * downstream constructs never re-derive defaults.
 */
export interface QuickRedirectConfig {
  /** Logical project name used for resource naming. */
  readonly projectName: ProjectName;
  /** Domain visitors browse to (e.g. `quick.example.com`). */
  readonly domainName: string;
  /** Absolute `https://` URL the domain 301-redirects to. */
  readonly redirectTargetUrl: string;
  /** Route 53 hosted zone name that owns `domainName` (defaults to `domainName`). */
  readonly hostedZoneDomain: string;
  /** When true, resources are retained on stack deletion. */
  readonly retainResources: boolean;
}

/**
 * Builds a resource name by prefixing the project name onto a resource suffix.
 *
 * @param projectName - The logical project name.
 * @param resourceName - The resource-specific suffix.
 * @returns A PascalCase resource name such as `QuickRedirectFunction`.
 */
export const createResourceName = (projectName: ProjectName, resourceName: ResourceName): string =>
  `${projectName}${resourceName}`;

/**
 * Normalises an arbitrary label into a PascalCase CDK construct id.
 *
 * @param resourceName - The label to normalise.
 * @returns The label with its first character upper-cased.
 */
export const createConstructId = (resourceName: string): string =>
  resourceName.charAt(0).toUpperCase() + resourceName.slice(1);

/**
 * Builds a CloudFormation stack name from the project name and a stack label.
 *
 * @param projectName - The logical project name.
 * @param stackName - The stack-specific label.
 * @returns A name such as `QuickRedirectQuickRedirectStack`.
 */
export const createStackName = (projectName: ProjectName, stackName: string): string => {
  const pascalStackName = stackName.charAt(0).toUpperCase() + stackName.slice(1);
  return `${projectName}${pascalStackName}Stack`;
};

/**
 * Maps a retain flag to the corresponding CDK removal policy.
 *
 * @param retain - Whether resources should survive stack deletion.
 * @returns `RemovalPolicy.RETAIN` when true, otherwise `RemovalPolicy.DESTROY`.
 */
export const getRemovalPolicy = (retain: boolean): RemovalPolicy =>
  retain ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;

/**
 * Reads and validates deployer-supplied CDK context into a
 * {@link QuickRedirectConfig}. Fails closed: any missing required key or an
 * invalid redirect target aborts synthesis with an actionable error naming the
 * offending key and showing example `-c` usage.
 */
export class ConfigParser {
  private static readonly EXAMPLE_DOMAIN = 'quick.example.com';
  private static readonly EXAMPLE_TARGET =
    'https://us-east-1.quicksight.aws.amazon.com/sn/auth/signin?directory_alias=your-quick-account-name';

  /**
   * Parses configuration from a context getter.
   *
   * @param getContext - Resolver for a context key (typically `app.node.tryGetContext`).
   * @returns A validated {@link QuickRedirectConfig}.
   * @throws Error when `domainName` is missing, `redirectTargetUrl` is missing,
   *   or `redirectTargetUrl` does not start with `https://`.
   */
  public parse(getContext: (key: string) => string | undefined): QuickRedirectConfig {
    const domainName = this.requireValue(getContext, ContextKey.DOMAIN_NAME);
    const redirectTargetUrl = this.requireHttpsUrl(getContext, ContextKey.REDIRECT_TARGET_URL);
    const hostedZoneDomain = getContext(ContextKey.HOSTED_ZONE_DOMAIN) ?? domainName;
    const retainResources = getContext(ContextKey.RETAIN) === 'true';

    return {
      projectName: ProjectName.QUICK_REDIRECT,
      domainName,
      redirectTargetUrl,
      hostedZoneDomain,
      retainResources,
    };
  }

  private requireValue(getContext: (key: string) => string | undefined, key: ContextKey): string {
    const value = getContext(key);
    if (!value) {
      throw new Error(
        `Missing required context '${key}'. Pass it at synth/deploy time, e.g.:\n` +
          `  npx cdk deploy -c ${ContextKey.DOMAIN_NAME}=${ConfigParser.EXAMPLE_DOMAIN} ` +
          `-c ${ContextKey.REDIRECT_TARGET_URL}='${ConfigParser.EXAMPLE_TARGET}'`
      );
    }
    return value;
  }

  private requireHttpsUrl(getContext: (key: string) => string | undefined, key: ContextKey): string {
    const value = this.requireValue(getContext, key);
    if (!value.startsWith('https://')) {
      throw new Error(
        `Context '${key}' must be an absolute https:// URL (got '${value}'). Example:\n` +
          `  npx cdk deploy -c ${ContextKey.DOMAIN_NAME}=${ConfigParser.EXAMPLE_DOMAIN} ` +
          `-c ${ContextKey.REDIRECT_TARGET_URL}='${ConfigParser.EXAMPLE_TARGET}'`
      );
    }
    return value;
  }
}
