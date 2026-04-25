import type { Plugin } from "@opencode-ai/plugin"

const validate: Plugin = async (ctx) => {
  const { $ } = ctx

  return {
    async "tool.execute.after"(input, output) {
      if (input.tool !== "write" && input.tool !== "edit") return

      const filePath: string | undefined =
        input.args?.filePath ?? input.args?.file_path

      if (!filePath) return

      const target = /knowledge\/articles\/.*\.json/
      if (!target.test(filePath)) return

      try {
        const result = await $`python3 hooks/validate_json.py ${filePath}`.nothrow()
        if (result.exitCode !== 0) {
          output.output = [
            output.output || "",
            `[validate plugin] ${filePath} 校验失败 (exit ${result.exitCode}):\n${result.stdout?.toString() || ""}${result.stderr?.toString() || ""}`,
          ]
            .filter(Boolean)
            .join("\n")
        }
      } catch (err) {
        // Shell spawn failure — log but do not block the agent
      }
    },
  }
}

export default validate
