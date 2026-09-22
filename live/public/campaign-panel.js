// Show the latest recorded campaign, preserving evidence quality and unknowns.
import { $, node, brief, dateFormat, actionName } from "./view-helpers.js";

const campaignRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
export function latestCampaign(list) {
  for (let index = list.length - 1; index >= 0; index--) {
    const event = list[index];
    if (!["observation", "result"].includes(event?.type)) continue;
    for (const [path, observation] of [
      ["observation.campaign", event.observation],
      ["result.observation.campaign", event.result?.observation],
      ["result.after.campaign", event.result?.after],
    ]) {
      if (
        campaignRecord(observation) &&
        Object.hasOwn(observation, "campaign")
      ) {
        return { event, path, observation, campaign: observation.campaign };
      }
    }
  }
  return null;
}
const navigationNames = {
  needs_target: "等待目标",
  needs_map_connection: "缺少地图连接",
  wait_for_map_transition: "等待地图切换",
  resolve_current_ui: "先处理当前界面",
  needs_geometry: "缺少地图几何信息",
  needs_target_coordinate: "缺少目标坐标",
  interact: "可以交互",
  face_object: "朝向目标对象",
  activate_transition: "准备切换地图",
  at_target: "已到目标位置",
  no_observed_path: "尚无已观察通路",
  path_to_target: "沿已观察路径推进",
  toward_unseen_target: "向未观察区域推进",
  closest_observed_approach: "接近目标并重新观察",
};
function campaignTarget(target, mapId) {
  const parts = [];
  if (mapId !== undefined && mapId !== null)
    parts.push(`主线目标地图 ${mapId}`);
  if (!campaignRecord(target)) return parts.join(" · ") || "未提供目标";
  const kinds = {
    object: "交互对象",
    coordinate: "坐标",
    warp: "出入口",
    connection: "地图边界",
    observed_transition: "已观察出口",
    unknown_route: "尚未知的路线",
  };
  if (target.kind) parts.push(kinds[target.kind] ?? target.kind);
  if (target.x !== undefined && target.y !== undefined)
    parts.push(`(${target.x}, ${target.y})`);
  if (target.sprite) parts.push(target.sprite);
  if (target.text_id !== undefined) parts.push(`对话 ID ${target.text_id}`);
  if (target.direction) parts.push(target.direction);
  const destination = target.destination_map_id ?? target.destination_map;
  if (destination !== undefined && destination !== null)
    parts.push(`→ ${target.destination_name ?? "地图"} ${destination}`);
  if (target.quality) parts.push(`来源质量：${target.quality}`);
  return parts.join(" · ") || "目标内容为空";
}
export function campaignFactLabel(record) {
  if (!campaignRecord(record))
    return record === null || record === undefined
      ? "未提供 / 尚未验证"
      : "直接记录值，未附验证说明";
  const quality = String(record.quality ?? "未注明质量");
  const unverified =
    record.verified === false || /unverified|prior/.test(quality);
  const verified =
    !unverified &&
    (record.verified === true || /^verified(?:_|$)/.test(quality));
  const label = verified ? "已验证" : "尚未验证";
  const source =
    typeof record.source === "string"
      ? record.source
      : record.source
        ? JSON.stringify(record.source)
        : "未提供来源";
  return `${label} · ${quality} · ${source}`;
}
function campaignChips(target, values, emptyText) {
  target.replaceChildren();
  if (!Array.isArray(values) || !values.length) {
    target.append(node("span", "empty-copy", emptyText));
    return;
  }
  for (const value of values)
    target.append(node("span", "campaign-chip", brief(value)));
}
export function renderCampaign(list, run) {
  const record = latestCampaign(list);
  const panel = $("campaign-panel");
  const signature = `${run.id}:${record?.event?.id ?? "none"}:${record?.path ?? ""}`;
  if (panel.dataset.signature === signature) return;
  panel.dataset.signature = signature;
  const campaign = record?.campaign;
  const available =
    campaignRecord(campaign) && Object.keys(campaign).length > 0;
  $("campaign-empty").hidden = available;
  $("campaign-body").hidden = !available;
  if (!available) {
    $("campaign-status").textContent = "尚无任务记录";
    $("campaign-status").className = "status-pill";
    return;
  }
  const objective = campaignRecord(campaign.active_objective)
    ? campaign.active_objective
    : {};
  const navigation = campaignRecord(campaign.navigation)
    ? campaign.navigation
    : {};
  const completed = Array.isArray(objective.completed_ids)
    ? objective.completed_ids
    : Array.isArray(campaign.completed_objectives)
      ? campaign.completed_objectives
      : null;
  const visited = Array.isArray(campaign.visited_map_ids)
    ? campaign.visited_map_ids
    : null;
  const connections = Array.isArray(campaign.observed_map_connections)
    ? campaign.observed_map_connections
    : null;
  const done =
    objective.id === "main_story_complete" &&
    objective.status === "completed" &&
    objective.completion === true;
  $("campaign-status").textContent = done
    ? "主线已验证完成"
    : ({ active: "目标推进中", needs_data: "目标需要验证数据" }[
        objective.status
      ] ?? brief(objective.status, "等待目标"));
  $("campaign-status").className =
    `status-pill ${done ? "success" : objective.status === "needs_data" ? "warning" : "active"}`;
  $("campaign-goal").textContent = brief(
    campaign.overall_goal,
    "此记录未提供长期目标",
  );
  $("campaign-source").textContent =
    `最后记录：第 ${record.event.step ?? "未提供"} 步 · ${dateFormat(record.event.time, true)} · ${record.path}`;
  $("campaign-objective-id").textContent = brief(
    objective.id,
    "目标 ID 未提供",
  );
  $("campaign-intent").textContent = brief(
    objective.intent,
    "尚未提供当前目标意图",
  );
  $("campaign-why").textContent = brief(
    objective.why,
    "此记录未提供目标说明。",
  );
  $("campaign-completion").textContent =
    objective.completion === true
      ? "完成证据已满足"
      : objective.completion === false
        ? "条件尚未满足"
        : "尚无足够验证证据";
  campaignChips(
    $("campaign-unknown"),
    objective.unknown_facts,
    Array.isArray(objective.unknown_facts)
      ? "记录中没有待验证事实项"
      : "未提供待验证事实列表",
  );

  const evidence = campaignRecord(objective.completion_evidence)
    ? Object.entries(objective.completion_evidence)
    : [];
  $("campaign-evidence").replaceChildren();
  $("campaign-evidence-empty").hidden = evidence.length > 0;
  for (const [name, fact] of evidence) {
    const row = node("tr");
    const value =
      campaignRecord(fact) && Object.hasOwn(fact, "value") ? fact.value : fact;
    row.append(
      node("td", "mono", name),
      node(
        "td",
        "mono",
        value === undefined ? "未提供" : JSON.stringify(value),
      ),
      node("td", "", campaignFactLabel(fact)),
    );
    $("campaign-evidence").append(row);
  }
  $("campaign-knowledge").replaceChildren();
  if (Array.isArray(objective.knowledge)) {
    for (const clue of objective.knowledge)
      $("campaign-knowledge").append(node("li", "", brief(clue)));
  }
  const source = objective.source;
  $("campaign-knowledge-source").textContent = campaignRecord(source)
    ? `目标知识来源：${source.repository ?? "未提供仓库"}${source.commit ? ` @ ${source.commit}` : ""} · ${source.quality ?? "未注明质量"}${source.full_source_binary_match === false ? " · 源码与 ROM 非逐字节一致" : ""}`
    : source
      ? `目标知识来源：${brief(source)}`
      : "此记录未附目标知识来源。";

  $("campaign-navigation-status").textContent =
    navigationNames[navigation.status] ??
    brief(navigation.status, "尚无导航记录");
  $("campaign-next-button").textContent =
    navigation.next_button === null || navigation.next_button === undefined
      ? "尚无建议"
      : actionName(navigation.next_button);
  $("campaign-target").textContent = campaignTarget(
    navigation.target ?? objective.target,
    objective.target_map_id,
  );
  const routeIds = Array.isArray(campaign.route_map_ids)
    ? campaign.route_map_ids
    : [];
  const routeNames = Array.isArray(campaign.route_map_names)
    ? campaign.route_map_names
    : [];
  $("campaign-route").textContent = routeIds.length
    ? routeIds
        .map((id, index) =>
          routeNames[index] ? `${routeNames[index]} (${id})` : `地图 ${id}`,
        )
        .join(" → ")
    : "尚无已记录地图路线";
  const path = Array.isArray(navigation.path_preview)
    ? navigation.path_preview
    : [];
  $("campaign-path").textContent = path.length
    ? path
        .map((waypoint) =>
          campaignRecord(waypoint)
            ? `(${brief(waypoint.x, "?")}, ${brief(waypoint.y, "?")})${waypoint.button ? ` ${actionName(waypoint.button)}` : ""}`
            : brief(waypoint),
        )
        .join(" → ")
    : "此记录没有路径预览";
  $("campaign-navigation-source").textContent = [
    navigation.source,
    navigation.limitations,
  ]
    .filter(Boolean)
    .map((value) => brief(value))
    .join(" ");
  const knownNames = new Map(
    routeIds.map((id, index) => [id, routeNames[index]]),
  );
  const world = record.observation.world;
  if (campaignRecord(world) && world.map_id !== undefined && world.name)
    knownNames.set(world.map_id, world.name);
  const mapLabel = (id) =>
    knownNames.get(id)
      ? `${knownNames.get(id)} (${id})`
      : `地图 ${brief(id, "未知")}`;
  const knownObjectives = new Map();
  for (const event of list) {
    const previous =
      event.type === "objective"
        ? event.objective
        : (event.observation?.campaign?.active_objective ??
          event.result?.observation?.campaign?.active_objective ??
          event.result?.after?.campaign?.active_objective);
    if (previous?.id && previous.intent)
      knownObjectives.set(previous.id, previous.intent);
  }
  campaignChips(
    $("campaign-completed"),
    completed?.map((id) =>
      knownObjectives.has(id) ? `${knownObjectives.get(id)} (${id})` : id,
    ),
    completed ? "尚无已验证完成目标" : "未提供完成目标列表",
  );
  campaignChips(
    $("campaign-visited"),
    visited?.map(mapLabel),
    visited ? "尚无到访地图记录" : "此观测未提供 visited_map_ids",
  );
  $("campaign-memory-counts").textContent =
    `${completed?.length ?? "—"} 个完成目标 · ${visited?.length ?? "—"} 张到访地图`;
  $("campaign-connections-note").textContent = connections
    ? `本次观测携带 ${connections.length} 条已观察连接；以下按记录顺序展示，未展示的历史不等于不存在。`
    : "此观测未提供持久地图连接。";
  $("campaign-connections").replaceChildren();
  for (const edge of connections ?? []) {
    const row = node("div", "campaign-connection");
    row.append(
      node(
        "strong",
        "",
        `${mapLabel(edge.from_map)} → ${mapLabel(edge.to_map)}`,
      ),
    );
    const origin = Array.isArray(edge.from_position)
      ? `(${edge.from_position.join(", ")})`
      : "起点未提供";
    const arrival = Array.isArray(edge.arrival)
      ? `(${edge.arrival.join(", ")})`
      : "落点未提供";
    row.append(
      node(
        "span",
        "",
        `${origin} → ${arrival} · ${actionName(edge.button)} · 步 ${edge.step ?? "未提供"} · ${brief(edge.source, "来源未提供")}`,
      ),
    );
    $("campaign-connections").append(row);
  }
  if (connections && !connections.length)
    $("campaign-connections").append(
      node("p", "empty-copy", "尚无观察到的跨地图连接。"),
    );
}
