const path = require('path');

const opencodeSkillPath = process.env.AI_USAGE_OPENCODE_SKILL_PATH || path.join(__dirname, '../opencode_skill/src');
const pythonPath = process.env.PYTHONPATH ? `${opencodeSkillPath}:${process.env.PYTHONPATH}` : opencodeSkillPath;

module.exports = {
  apps: [
    {
      name: 'ai-usage-dashboard',
      cwd: __dirname,
      script: '.venv/bin/python',
      args: '-m uvicorn local_display_service:app --host 0.0.0.0 --port 7995',
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONPATH: pythonPath,
      },
      autorestart: true,
      time: true,
    },
  ],
};
