import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'HarnessLearn',
  description: '从 0 到 1 构建 Agent Harness，每次只加一个机制',
  lang: 'zh-CN',

  // 部署到 GitHub Pages 时需要设置 base
  base: '/HarnessLearn/',

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/HarnessLearn/harness.svg' }],
  ],

  themeConfig: {
    logo: '/harness.svg',
    siteTitle: 'HarnessLearn',

    nav: [
      { text: '首页', link: '/' },
      { text: '学习路径', link: '/timeline' },
      { text: 'GitHub', link: 'https://github.com/zhuliyi10/HarnessLearn' },
    ],

    sidebar: [
      {
        text: '基础能力',
        items: [
          { text: 's01: Agent Loop', link: '/s01_agent_loop/' },
          { text: 's02: Tool Use', link: '/s02_tool_use/' },
          { text: 's03: Permission', link: '/s03_permission/' },
          { text: 's04: Hooks', link: '/s04_hooks/' },
        ],
      },
      {
        text: '复杂任务',
        items: [
          { text: 's05: TodoWrite', link: '/s05_todo_write/' },
          { text: 's06: Subagent', link: '/s06_subagent/' },
          { text: 's07: Skill Loading', link: '/s07_skill_loading/' },
          { text: 's08: Context Compact', link: '/s08_context_compact/' },
        ],
      },
      {
        text: '记忆',
        items: [
          { text: 's09: Memory', link: '/s09_memory/' },
        ],
      },
      {
        text: '长期运行',
        items: [
          { text: 's10: Task System', link: '/s10_task_system/' },
          { text: 's11: Background Tasks', link: '/s11_background_tasks/' },
          { text: 's12: Cron Scheduler', link: '/s12_cron_scheduler/' },
        ],
      },
      {
        text: '协作与扩展',
        items: [
          { text: 's13: Agent Teams', link: '/s13_agent_teams/' },
          { text: 's14: MCP Plugin', link: '/s14_mcp_plugin/' },
        ],
      },
      {
        text: '集成收口',
        items: [
          { text: 's15: Integrated Harness', link: '/s15_integrated_harness/' },
          { text: 's16: Workflow Runtime', link: '/s16_workflow_runtime/' },
          { text: 's17: Goal Loop', link: '/s17_goal_loop/' },
        ],
      },
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/zhuliyi10/HarnessLearn' },
    ],

    outline: {
      level: [2, 3],
      label: '本页目录',
    },

    docFooter: {
      prev: '上一章',
      next: '下一章',
    },

    lastUpdated: {
      text: '最后更新',
    },

    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索', buttonAriaLabel: '搜索' },
          modal: {
            noResultsText: '无匹配结果',
            resetButtonTitle: '清除查询条件',
            footer: { selectText: '选择', navigateText: '切换' },
          },
        },
      },
    },
  },

  // 排除不需要处理的目录
  srcExclude: ['node_modules/**', '.venv/**', '**/code.py', '**/.env*'],

  // 忽略指向代码文件的链接（不是 md 页面）
  ignoreDeadLinks: [
    /\.py$/,
    /\.json$/,
    /\.txt$/,
  ],

  markdown: {
    lineNumbers: true,
  },
})
