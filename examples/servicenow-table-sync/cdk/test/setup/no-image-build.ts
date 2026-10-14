/**
 * Jest setup: keep CDK unit tests off the container runtime.
 *
 * `DockerImageCode.fromImageAsset` computes its asset hash by building the image
 * during synth, which shells out to finch/docker and blocks a headless runner. A
 * unit test only asserts on the synthesized template, so it is swapped for a
 * registry-backed image with no build (same `PackageType: Image` and the same
 * `ImageConfig.Command`, since the handler CMD is forwarded).
 *
 * `cdk synth` / `cdk deploy` do not load this file, so they build the real image.
 */
import { Stack } from "aws-cdk-lib";
import { Repository } from "aws-cdk-lib/aws-ecr";
import { DockerImageCode } from "aws-cdk-lib/aws-lambda";

let stubRepositoryStack: Stack | undefined;

beforeAll(() => {
  jest
    .spyOn(DockerImageCode, "fromImageAsset")
    .mockImplementation((_directory: string, props?: { readonly cmd?: string[] }) => {
      stubRepositoryStack ??= new Stack(undefined, "StubImageRepositoryStack");
      const repository = Repository.fromRepositoryName(
        stubRepositoryStack,
        `StubImageRepo${Math.random().toString(36).slice(2)}`,
        "stub-image-repo",
      );
      return DockerImageCode.fromEcr(repository, props?.cmd ? { cmd: props.cmd } : undefined);
    });
});

afterAll(() => {
  jest.restoreAllMocks();
  stubRepositoryStack = undefined;
});
