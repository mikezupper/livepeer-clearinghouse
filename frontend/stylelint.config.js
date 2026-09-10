export default {
  extends: ["stylelint-config-standard"],
  ignoreFiles: ["**/coverage/**", "**/dist/**"],
  rules: {
    "custom-property-pattern": "^och-|^admin-",
    "declaration-property-value-no-unknown": true
  }
}
