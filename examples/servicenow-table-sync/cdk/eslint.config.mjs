// Minimal lint layer: prettier owns formatting; eslint enforces the destructuring
// convention. We intentionally do NOT enable the full typescript-eslint recommended
// set here.
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["cdk.out", "coverage", "node_modules", "**/*.js", "**/*.d.ts"],
  },
  {
    files: ["lib/**/*.ts", "test/**/*.ts"],
    languageOptions: {
      parser: tseslint.parser,
    },
    rules: {
      "prefer-destructuring": [
        "error",
        { array: true, object: true },
        { enforceForRenamedProperties: false },
      ],
    },
  },
);
