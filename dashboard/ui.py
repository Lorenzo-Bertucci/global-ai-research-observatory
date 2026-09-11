"""Shared visual conventions and compact result presentation helpers."""

from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


NAVY = "#16324F"
BLUE = "#2E6F9E"
TEAL = "#3C8D87"
GOLD = "#C79845"
SLATE = "#66788A"
PALETTE = [BLUE, TEAL, GOLD, "#765D93", "#B65F5A", "#577590", "#7A9E62"]


def apply_figure_style(figure: go.Figure, height: int = 410) -> go.Figure:
    figure.update_layout(
        height=height,
        margin=dict(l=10, r=15, t=55, b=15),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", color="#263746", size=13),
        title=dict(font=dict(size=18, color=NAVY), x=0),
        colorway=PALETTE,
        legend=dict(title=None, orientation="h", yanchor="bottom", y=1.02, x=0),
        hoverlabel=dict(bgcolor="white", font_size=13),
    )
    figure.update_xaxes(showgrid=True, gridcolor="rgba(41,67,89,.10)", zeroline=False)
    figure.update_yaxes(showgrid=False, zeroline=False)
    return figure


def horizontal_bar(
    frame: pd.DataFrame,
    *,
    x: str,
    y: str,
    title: str,
    x_title: str,
    color: str = BLUE,
    height: int = 430,
    hover_data: dict[str, str | bool] | None = None,
) -> go.Figure:
    ordered = frame.sort_values(x, ascending=True)
    figure = px.bar(
        ordered,
        x=x,
        y=y,
        orientation="h",
        title=title,
        color_discrete_sequence=[color],
        hover_data=hover_data,
    )
    figure.update_layout(xaxis_title=x_title, yaxis_title=None)
    return apply_figure_style(figure, height=height)


def line_chart(
    frame: pd.DataFrame,
    *,
    x: str,
    y: str,
    title: str,
    y_title: str,
    color: str | None = None,
    markers: bool = True,
    height: int = 410,
) -> go.Figure:
    figure = px.line(
        frame,
        x=x,
        y=y,
        color=color,
        markers=markers,
        title=title,
        color_discrete_sequence=PALETTE,
    )
    figure.update_layout(xaxis_title=None, yaxis_title=y_title)
    figure.update_xaxes(dtick=1)
    return apply_figure_style(figure, height=height)


def empty_state(message: str = "No data available for the selected filters.") -> None:
    st.info(message, icon="ℹ️")


def compact_table(
    frame: pd.DataFrame,
    *,
    key: str,
    file_name: str,
    column_config: dict | None = None,
    height: int = 360,
) -> None:
    st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        height=height,
        column_config=column_config,
    )
    st.download_button(
        "Download aggregated CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
        key=key,
        type="tertiary",
    )


def safe_file_stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
