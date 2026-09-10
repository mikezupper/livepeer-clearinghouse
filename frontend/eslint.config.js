import eslint from "@eslint/js"
import lit from "eslint-plugin-lit"
import wc from "eslint-plugin-wc"
import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["**/dist/**", "**/coverage/**", "**/custom-elements.json"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/src/**/*.ts"],
    plugins: { lit, wc },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
      "no-console": "error",
      "no-restricted-imports": ["error", {
        "paths": [
          { "name": "lodash", "message": "Use Effect data modules." },
          { "name": "lit/directives/style-map.js", "message": "Inline styles are prohibited." }
        ]
      }],
      "no-restricted-syntax": ["error",
        { "selector": "ThrowStatement", "message": "Return a tagged error through Effect." },
        { "selector": "TryStatement", "message": "Wrap interop once with Effect.try or Effect.tryPromise." },
        { "selector": "FunctionDeclaration[async=true], ArrowFunctionExpression[async=true], MethodDefinition[value.async=true]", "message": "Compose Effect values instead of async functions." }
      ],
      "lit/no-invalid-html": "error",
      "lit/no-legacy-template-syntax": "error",
      "lit/no-property-change-update": "error",
      "lit/no-template-arrow": "error",
      "lit/no-useless-template-literals": "error",
      "wc/guard-super-call": "error",
      "wc/no-constructor-attributes": "error",
      "wc/no-self-class": "error"
    }
  },
  {
    files: ["**/*.test.ts"],
    rules: {
      "no-restricted-syntax": "off"
    }
  }
)
