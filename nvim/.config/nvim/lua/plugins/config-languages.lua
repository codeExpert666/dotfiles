-- 补齐配置文件的识别与 XML LSP；JSON/YAML/Docker 由官方 Extras 提供。
return {
  {
    "neovim/nvim-lspconfig",
    init = function()
      vim.filetype.add({
        extension = { mobileconfig = "xml" },
        filename = {
          ["compose.yaml"] = "yaml.docker-compose",
          ["compose.yml"] = "yaml.docker-compose",
          ["docker-compose.yaml"] = "yaml.docker-compose",
          ["docker-compose.yml"] = "yaml.docker-compose",
        },
        pattern = {
          ["compose%..+%.ya?ml"] = "yaml.docker-compose",
          ["docker%-compose%..+%.ya?ml"] = "yaml.docker-compose",
        },
      })
      -- 文本 plist 由 Neovim 按 XML 内容识别，不强制处理二进制 plist。
    end,
    opts = function(_, opts)
      opts.servers.lemminx = {}
      if LazyVim.has_extra("lang.yaml") and LazyVim.has_extra("lang.docker") then
        -- yamlls 负责 YAML/Schema 诊断，避免 Compose 服务重复报告同一语法错误。
        opts.servers.docker_compose_language_service.capabilities = {
          experimental = {
            alternateYamlLanguageService = {
              syntaxValidation = true,
              schemaValidation = true,
              formatting = true,
            },
          },
        }
      end
    end,
  },
}
