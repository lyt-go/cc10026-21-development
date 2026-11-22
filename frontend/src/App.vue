<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">数据服务开放与订阅管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向数据API注册、查询路由、订阅审批、用量计量与SLA保障的企业数据服务开放平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "API注册", path: "/api_registry" }, { label: "查询路由", path: "/query_route" }, { label: "订阅管理", path: "/subscription" }, { label: "频率限制", path: "/rate_limiter" }, { label: "用量计量", path: "/usage_billing" }, { label: "订阅账期用量明细", path: "/subscription-usage" }, { label: "缓存分层", path: "/cache_layer" }, { label: "数据过滤", path: "/data_filter" }, { label: "字段脱敏", path: "/field_mask" }, { label: "SLA监控", path: "/sla_monitor" }, { label: "结果格式化", path: "/result_format" }, { label: "查询优化", path: "/query_optimize" }, { label: "异常归类", path: "/error_classify" }, { label: "数据归并", path: "/data_merge" }, { label: "导出任务", path: "/export_task" }, { label: "调用审计", path: "/call_audit" }, { label: "版本兼容", path: "/version_compat" }, { label: "批量查询", path: "/batch_query" }, { label: "SDK配置", path: "/sdk_config" }]
</script>
