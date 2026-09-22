// Continuous game video is independent of JSONL/SSE updates and game controls.
import { $, terminalStatus } from "./view-helpers.js";

export function createVideoPlayer() {
  let currentRun = null;
  const videoState = {
    key: null,
    hls: null,
    attached: false,
    retry: null,
    retries: 0,
    token: 0,
    hasPicture: false,
  };

  function videoStatus(message, kind = "") {
    $("video-status").textContent = message;
    $("video-status").className = kind;
  }
  function videoPlaceholder(title, message) {
    $("video-placeholder").hidden = false;
    $("video-placeholder-title").textContent = title;
    $("video-placeholder-note").textContent = message;
  }
  function releaseVideo() {
    videoState.token++;
    clearTimeout(videoState.retry);
    videoState.retry = null;
    videoState.hls?.destroy();
    videoState.hls = null;
    videoState.attached = false;
    videoState.hasPicture = false;
    const video = $("game-video");
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  function scheduleVideoRetry(message) {
    if (videoState.retry) return;
    const run = currentRun;
    if (!run?.video?.enabled) return;
    const seconds = Math.min(15, 2 ** Math.min(4, ++videoState.retries));
    videoStatus(`${message} · ${seconds} 秒后重连`, "video-error");
    if (!videoState.hasPicture)
      videoPlaceholder(
        "画面暂时不可用",
        "视频连接正在自动重试，调用记录仍独立更新。",
      );
    const key = videoState.key;
    videoState.retry = setTimeout(() => {
      videoState.retry = null;
      if (videoState.key === key) attachVideo(currentRun);
    }, seconds * 1000);
  }
  function playVideo(token) {
    const promise = $("game-video").play();
    promise?.catch((error) => {
      if (token !== videoState.token) return;
      if (error.name === "NotAllowedError")
        videoStatus(
          "浏览器暂停了自动播放，请点击画面上的播放按钮。",
          "video-pending",
        );
      else if (error.name !== "AbortError")
        scheduleVideoRetry("视频播放暂不可用");
    });
  }
  function attachVideo(run) {
    if (!run?.video?.url || !run.video.enabled) return;
    releaseVideo();
    videoState.attached = true;
    const token = videoState.token;
    const video = $("game-video");
    video.muted = true;
    videoStatus("正在缓冲游戏视频…", "video-pending");
    videoPlaceholder("连接游戏画面", "首次播放需要等待视频片段就绪。");
    // Some Chrome builds report native HLS as "maybe" without playing it.
    // Prefer the verified MSE implementation; Safari can use native fallback.
    if (window.Hls?.isSupported()) {
      const hls = new window.Hls({
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 15,
        maxBufferLength: 10,
        liveSyncDurationCount: 2,
        liveMaxLatencyDurationCount: 5,
      });
      videoState.hls = hls;
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        if (token === videoState.token) playVideo(token);
      });
      hls.on(window.Hls.Events.ERROR, (_, data) => {
        if (token !== videoState.token) return;
        if (data.fatal) scheduleVideoRetry("视频连接中断");
        else if (data.details === "bufferStalledError")
          videoStatus("视频缓冲中 · 模型事件仍独立更新", "video-pending");
      });
      hls.loadSource(run.video.url);
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = run.video.url;
      playVideo(token);
    } else {
      videoStatus(
        "此浏览器不支持 HLS 视频，请使用当前版本的 Safari、Chrome 或 Edge。",
        "video-error",
      );
      videoPlaceholder(
        "浏览器暂不支持此视频",
        "可继续查看下方实时调用与执行记录。",
      );
    }
  }
  function update(run) {
    currentRun = run;
    const key = run ? `${run.id}:${run.video?.url ?? ""}` : null;
    if (videoState.key !== key) {
      releaseVideo();
      videoState.key = key;
      videoState.retries = 0;
    }
    if (!run) return;
    if (!run.video?.enabled) {
      $("video-badge").textContent = "无录像";
      $("video-retry").disabled = true;
      videoStatus("这轮运行只保留了调用与执行记录。");
      videoPlaceholder(
        "这轮没有录制游戏画面",
        "选择最新的视频直播运行，即可观看 Pokémon 界面。",
      );
      return;
    }
    $("video-retry").disabled = false;
    $("video-badge").textContent = terminalStatus(run.status)
      ? "已结束 · 视频片段"
      : "LIVE · 游戏画面";
    if (!videoState.attached && run.video.available) attachVideo(run);
    else if (!videoState.attached) {
      videoStatus("等待游戏视频流就绪…", "video-pending");
      videoPlaceholder(
        "视频直播正在启动",
        "画面就绪后会自动播放，无需刷新页面。",
      );
    }
  }
  $("video-retry").addEventListener("click", () => {
    videoState.retries = 0;
    attachVideo(currentRun);
  });
  $("game-video").addEventListener("loadeddata", () => {
    if (!videoState.attached) return;
    videoState.hasPicture = true;
    $("video-placeholder").hidden = true;
  });
  $("game-video").addEventListener("playing", () => {
    if (!videoState.attached) return;
    clearTimeout(videoState.retry);
    videoState.retry = null;
    videoState.retries = 0;
    videoState.hasPicture = true;
    $("video-placeholder").hidden = true;
    videoStatus("视频正在播放 · 游戏动作会随 JEV 决策推进", "video-playing");
  });
  $("game-video").addEventListener("waiting", () => {
    if (videoState.attached && !videoState.retry)
      videoStatus("视频缓冲中…", "video-pending");
  });
  $("game-video").addEventListener("stalled", () => {
    if (videoState.attached) scheduleVideoRetry("视频数据暂时中断");
  });
  $("game-video").addEventListener("error", () => {
    if (videoState.attached) scheduleVideoRetry("视频连接暂不可用");
  });
  $("game-video").addEventListener("pause", () => {
    if (videoState.attached && videoState.hasPicture && !$("game-video").ended)
      videoStatus("画面已暂停 · 点击播放可继续观看");
  });
  $("game-video").addEventListener("ended", () => {
    if (!videoState.attached) return;
    const run = currentRun;
    if (run && !terminalStatus(run.status))
      scheduleVideoRetry("视频编码器正在重新连接");
    else videoStatus("已播放到本轮保留视频的结尾");
  });
  return { update };
}
