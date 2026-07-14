module.exports = {
  apps: [
    {
      name: "video-api",
      script: "C:\\Python312\\python.exe",
      args: "-u -m uvicorn app_api:app --host 0.0.0.0 --port 8288 --log-level info",
      interpreter: "none",
      cwd: "C:\\Users\\LHDA\\Documents\\Code\\Video-Dewatermark-and-Upscale",

      // Auto-restart on crash
      autorestart: true,
      watch: false,

      // Restart policy
      restart_delay: 3000,
      max_restarts: 10,
      min_uptime: "10s",

      // Environment
      env: {
        PYTHONIOENCODING: "utf-8",
        PYTHONUNBUFFERED: "1"
      },

      // Logs — rotate at 50MB, keep 7 days
      out_file: "logs\\api_out.log",
      error_file: "logs\\api_err.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: false,
      max_size: "50M",
      retain: 7
    }
  ]
};
