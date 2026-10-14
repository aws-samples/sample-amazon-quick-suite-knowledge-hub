module.exports = {
  testEnvironment: "node",
  roots: ["<rootDir>/test"],
  testMatch: ["**/*.test.ts"],
  transform: { "^.+\\.tsx?$": "ts-jest" },
  setupFilesAfterEnv: ["<rootDir>/test/setup/no-image-build.ts"],
};
