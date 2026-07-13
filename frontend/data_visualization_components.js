(function() {
  "use strict";

  var YEAR_COLORS = [
    "#2563eb", "#dc2626", "#16a34a", "#ca8a04", "#7c3aed", "#0891b2",
    "#db2777", "#65a30d", "#f97316", "#0f766e", "#9333ea", "#b91c1c",
    "#1d4ed8", "#15803d", "#a16207", "#be123c", "#0369a1", "#4f46e5",
    "#c2410c", "#047857", "#a21caf", "#0e7490", "#7f1d1d", "#365314"
  ];

  function calendarDayIndex(month, day) {
    return Math.floor((Date.UTC(2000, month - 1, day) - Date.UTC(2000, 0, 1)) / 86400000) + 1;
  }

  var calendarMonthTicks = Array.from({ length: 12 }, function(_, index) {
    return { value: calendarDayIndex(index + 1, 1), label: (index + 1) + "月" };
  });

  function renderCheckboxOptions(container, items, onChange, checkedDefault) {
    container.innerHTML = "";
    items.forEach(function(item) {
      var label = document.createElement("label");
      label.className = "dv-checkbox-label";
      label.dataset.dvComponent = "checkbox-option";
      var checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(item);
      checkbox.checked = !!checkedDefault;
      checkbox.addEventListener("change", onChange);
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(String(item)));
      container.appendChild(label);
    });
  }

  function setCheckboxes(container, checked) {
    container.querySelectorAll('input[type="checkbox"]').forEach(function(checkbox) {
      checkbox.checked = checked;
    });
  }

  function bindCheckboxPanelActions(container, allButton, noneButton, onChange) {
    allButton.onclick = function() {
      setCheckboxes(container, true);
      onChange();
    };
    noneButton.onclick = function() {
      setCheckboxes(container, false);
      onChange();
    };
  }

  function renderPagination(container, options) {
    if (!container) return;
    options = options || {};
    var pageSizes = options.pageSizes || [20, 50, 100];
    var pageSize = Number(options.pageSize) || pageSizes[0];
    var total = Math.max(0, Number(options.total) || 0);
    var totalPages = Math.max(1, Math.ceil(total / pageSize));
    var page = Math.max(1, Math.min(Number(options.page) || 1, totalPages));
    container.innerHTML = '<div class="tm-pagination" data-dv-component="server-pagination">' +
      "<span>共 " + total + " 条</span>" +
      '<label>每页<select data-page-size>' +
      pageSizes.map(function(size) {
        return '<option value="' + size + '"' + (size === pageSize ? " selected" : "") + ">" + size + "</option>";
      }).join("") +
      "</select>条</label>" +
      '<button type="button" data-page-action="prev"' + (page <= 1 ? " disabled" : "") + ">上一页</button>" +
      "<span>第 " + page + " / " + totalPages + " 页</span>" +
      '<button type="button" data-page-action="next"' + (page >= totalPages ? " disabled" : "") + ">下一页</button>" +
      "</div>";
    var pageSizeSelect = container.querySelector("[data-page-size]");
    var previousButton = container.querySelector('[data-page-action="prev"]');
    var nextButton = container.querySelector('[data-page-action="next"]');
    pageSizeSelect.onchange = function() {
      if (options.onPageSizeChange) options.onPageSizeChange(Number(pageSizeSelect.value));
    };
    previousButton.onclick = function() {
      if (page > 1 && options.onPageChange) options.onPageChange(page - 1);
    };
    nextButton.onclick = function() {
      if (page < totalPages && options.onPageChange) options.onPageChange(page + 1);
    };
  }

  function buildYearColorMap(years) {
    var map = {};
    years.forEach(function(year, index) {
      map[year] = YEAR_COLORS[index % YEAR_COLORS.length];
    });
    return map;
  }

  function renderYearLegend(element, years, yearColorMap, highlightedYear) {
    if (!element) return;
    element.innerHTML = years.map(function(year) {
      var stateClass = highlightedYear
        ? (highlightedYear === year ? " selected" : " dimmed")
        : "";
      return '<span class="dv-year-legend-item' + stateClass + '"><span class="dv-year-legend-swatch" style="background:' +
        yearColorMap[year] + '"></span>' + year + "</span>";
    }).join("");
  }

  function drawAxisTicks(ctx, ticks, xScale, y) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ticks.forEach(function(tick) {
      ctx.fillText(tick.label, xScale(tick.value), y);
    });
  }

  function drawMissingMarker(ctx, x, y, color, radius) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - radius + 1, y + radius - 1);
    ctx.lineTo(x + radius - 1, y - radius + 1);
    ctx.stroke();
    ctx.restore();
  }

  function distanceToSegment(px, py, segment) {
    var dx = segment.x2 - segment.x1;
    var dy = segment.y2 - segment.y1;
    if (dx === 0 && dy === 0) return Math.hypot(px - segment.x1, py - segment.y1);
    var ratio = ((px - segment.x1) * dx + (py - segment.y1) * dy) / (dx * dx + dy * dy);
    ratio = Math.max(0, Math.min(1, ratio));
    return Math.hypot(px - (segment.x1 + ratio * dx), py - (segment.y1 + ratio * dy));
  }

  function closestPoint(hitPoints, x, y, maxDistance) {
    var closest = null;
    var distance = maxDistance;
    hitPoints.forEach(function(hit) {
      var current = Math.hypot(x - hit.x, y - hit.y);
      if (current < distance) {
        closest = hit;
        distance = current;
      }
    });
    return closest;
  }

  function closestLine(hitSegments, x, y, maxDistance) {
    var closest = null;
    var distance = maxDistance;
    hitSegments.forEach(function(segment) {
      var current = distanceToSegment(x, y, segment);
      if (current < distance) {
        closest = segment;
        distance = current;
      }
    });
    return closest;
  }

  function eventPoint(canvas, event) {
    var rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (canvas.clientWidth / rect.width),
      y: (event.clientY - rect.top) * (canvas.clientHeight / rect.height),
    };
  }

  function hideTooltip(canvas, tooltipElement) {
    canvas.title = "";
    if (tooltipElement) tooltipElement.classList.add("hidden");
  }

  function showTooltip(canvas, tooltipElement, html, event) {
    if (!tooltipElement) return;
    tooltipElement.innerHTML = html;
    tooltipElement.classList.remove("hidden");
    var containerRect = canvas.parentElement.getBoundingClientRect();
    var left = event.clientX - containerRect.left + 14;
    var top = event.clientY - containerRect.top + 14;
    left = Math.min(left, containerRect.width - tooltipElement.offsetWidth - 8);
    top = Math.min(top, containerRect.height - tooltipElement.offsetHeight - 8);
    tooltipElement.style.left = Math.max(8, left) + "px";
    tooltipElement.style.top = Math.max(8, top) + "px";
  }

  function renderYearSmallMultiples(options) {
    var canvas = options.canvas;
    var series = options.series || {};
    var products = options.products || Object.keys(series);
    var state = options.state;
    var pointValue = options.pointValue || function(point) { return Number(point.value); };
    var pointX = options.pointX;
    var isMissing = options.isMissing || function(point) {
      return !point || point.value === null || point.value === undefined || point.value === "" || !Number.isFinite(Number(point.value));
    };
    var axisTicks = options.axisTicks || [];
    var xMin = Number(options.xMin);
    var xMax = Number(options.xMax);
    var container = canvas.parentElement;
    var width = options.width || Math.max(320, container.clientWidth);
    var columns = width >= 1100 ? 3 : (width >= 760 ? 2 : 1);
    var gap = 28;
    var panelHeight = 190;
    var rows = Math.max(1, Math.ceil(products.length / columns));
    var height = Math.max(420, rows * panelHeight + (rows - 1) * gap);
    var dpr = window.devicePixelRatio || 1;

    canvas.dataset.dvComponent = "year-small-multiples";
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    hideTooltip(canvas, options.tooltipElement);

    var years = [];
    products.forEach(function(product) {
      Object.keys(series[product] || {}).forEach(function(year) {
        if (years.indexOf(year) < 0) years.push(year);
      });
    });
    years.sort();
    if (state.highlightedYear && years.indexOf(state.highlightedYear) < 0) state.highlightedYear = null;
    state.highlightedLineKey = null;
    var yearColorMap = buildYearColorMap(years);
    renderYearLegend(options.legendElement, years, yearColorMap, state.highlightedYear);

    if (!products.length) {
      ctx.fillStyle = "#9ca3af";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(options.emptyMessage || "暂无数据", width / 2, 190);
      return;
    }

    var panelWidth = (width - gap * (columns - 1)) / columns;
    var hitPoints = [];
    var hitSegments = [];
    products.forEach(function(product, index) {
      var col = index % columns;
      var row = Math.floor(index / columns);
      var originX = col * (panelWidth + gap);
      var originY = row * (panelHeight + gap);
      var pad = { top: 22, right: 34, bottom: 28, left: 40 };
      var chartWidth = panelWidth - pad.left - pad.right;
      var chartHeight = panelHeight - pad.top - pad.bottom;
      var productSeries = series[product] || {};
      var values = [];
      Object.keys(productSeries).forEach(function(year) {
        (productSeries[year] || []).forEach(function(point) {
          if (!isMissing(point)) values.push(pointValue(point));
        });
      });
      if (!values.length) return;

      var yMin = Math.min.apply(null, values);
      var yMax = Math.max.apply(null, values);
      if (options.includeZero) {
        yMin = Math.min(0, yMin);
        yMax = Math.max(0, yMax);
      }
      var yPadding = (yMax - yMin) * 0.08 || 20;
      yMin = options.clampFloorZero ? Math.max(0, yMin - yPadding) : yMin - yPadding;
      yMax += yPadding;
      if (yMax === yMin) yMax = yMin + 1;

      function xScale(value) {
        return originX + pad.left + ((value - xMin) / (xMax - xMin)) * chartWidth;
      }
      function yScale(value) {
        return originY + pad.top + chartHeight - ((value - yMin) / (yMax - yMin)) * chartHeight;
      }

      ctx.fillStyle = "#111827";
      ctx.font = "bold 12px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(product, originX + pad.left, originY + 14);
      ctx.strokeStyle = "#e5e7eb";
      ctx.lineWidth = 1;
      for (var grid = 0; grid < 4; grid += 1) {
        var gridY = originY + pad.top + (grid / 3) * chartHeight;
        ctx.beginPath();
        ctx.moveTo(originX + pad.left, gridY);
        ctx.lineTo(originX + pad.left + chartWidth, gridY);
        ctx.stroke();
      }
      if (options.drawZeroAxis && yMin <= 0 && yMax >= 0) {
        var zeroY = yScale(0);
        ctx.save();
        ctx.strokeStyle = "#64748b";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(originX + pad.left, zeroY);
        ctx.lineTo(originX + pad.left + chartWidth, zeroY);
        ctx.stroke();
        ctx.fillStyle = "#64748b";
        ctx.font = "10px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText("0", originX + pad.left - 6, zeroY + 4);
        ctx.restore();
      }
      drawAxisTicks(ctx, axisTicks, xScale, originY + pad.top + chartHeight + 18);

      Object.keys(productSeries).sort().forEach(function(year) {
        var points = productSeries[year] || [];
        var color = yearColorMap[year];
        var selected = state.highlightedYear === year;
        var dimmed = state.highlightedYear && !selected;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.globalAlpha = dimmed ? 0.14 : 1;
        ctx.lineWidth = selected ? 2.6 : 1.4;
        ctx.beginPath();
        var previous = null;
        points.forEach(function(point) {
          if (isMissing(point)) {
            previous = null;
            return;
          }
          var x = xScale(pointX(point));
          var y = yScale(pointValue(point));
          if (previous) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
          var hit = { x: x, y: y, year: year, product: product, point: point };
          hitPoints.push(hit);
          if (previous) {
            hitSegments.push({
              x1: previous.x, y1: previous.y, x2: x, y2: y,
              year: year, product: product, point: point,
            });
          }
          previous = hit;
        });
        ctx.stroke();

        if (options.drawMissingPoints) {
          points.forEach(function(point) {
            if (!isMissing(point)) return;
            var x = xScale(pointX(point));
            var y = originY + pad.top + chartHeight - 7;
            drawMissingMarker(ctx, x, y, color, selected ? 4 : 3);
            hitPoints.push({ x: x, y: y, year: year, product: product, point: point });
          });
        }
        if (selected) {
          ctx.fillStyle = color;
          points.forEach(function(point) {
            if (isMissing(point)) return;
            ctx.beginPath();
            ctx.arc(xScale(pointX(point)), yScale(pointValue(point)), 2.5, 0, Math.PI * 2);
            ctx.fill();
          });
        }
        ctx.restore();
      });
    });

    canvas.onclick = function(event) {
      var point = eventPoint(canvas, event);
      var hit = closestLine(hitSegments, point.x, point.y, 10) || closestPoint(hitPoints, point.x, point.y, 12);
      if (!hit) return;
      state.highlightedYear = state.highlightedYear === hit.year ? null : hit.year;
      if (options.onHighlight) options.onHighlight(state.highlightedYear);
    };
    canvas.onmousemove = function(event) {
      var point = eventPoint(canvas, event);
      var hit = closestPoint(hitPoints, point.x, point.y, options.tooltipDistance || 16);
      if (!hit) {
        hideTooltip(canvas, options.tooltipElement);
        return;
      }
      if (options.tooltipElement && options.tooltipHtml) {
        showTooltip(canvas, options.tooltipElement, options.tooltipHtml(hit), event);
      } else if (options.tooltipText) {
        canvas.title = options.tooltipText(hit);
      }
    };
    canvas.onmouseleave = function() {
      hideTooltip(canvas, options.tooltipElement);
    };
  }

  window.DataVisualizationComponents = {
    bindCheckboxPanelActions: bindCheckboxPanelActions,
    calendarMonthTicks: calendarMonthTicks,
    renderCheckboxOptions: renderCheckboxOptions,
    renderPagination: renderPagination,
    renderYearSmallMultiples: renderYearSmallMultiples,
    yearColors: YEAR_COLORS,
  };
})();
