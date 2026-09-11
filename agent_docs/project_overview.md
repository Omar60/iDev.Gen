# Project Overview

## Purpose

iDev.Gen is a local photo-session application for LoRA character models,
running on top of ComfyUI. Its core model is **Model (character) -> Session ->
Shots**: a model supplies identity, a session holds a look, and shots vary the
take.

The application configures ComfyUI workflows and models, creates and runs
photo sessions, supports reference and guided takes, manages resource and room
libraries, composes and judges catalogue-based shots, rates and reshoots
images, and provides a cross-session slideshow. It also includes local setup,
backup, import, translation, and troubleshooting workflows.

## Scope

The project covers local authoring and operation of generated photo sessions;
it does not provide model loading, hosted multi-user service behavior, or
authentication.

## Architecture

This is a single-user local web application. A React frontend calls the
FastAPI backend, which coordinates SQLite persistence, prompt/session services,
and the ComfyUI client. The serial runner queues one shot at a time and moves
finished files from ComfyUI output into the session's data folder.

Legacy room/catalogue workflows and the newer `resource-v1` session-planning
workflow coexist. Resource storage keeps accepted source entries and immutable
revisions separate from translation and readiness data; the legacy Rooms path
continues to serve measured catalogue and room-seed use cases.

## Main Workflows

- Configure ComfyUI, output/data folders, optional LoRA previews, and optional
  prompt-assistant settings.
- Import a ComfyUI API-format workflow, verify its node mapping, define a model,
  and create a legacy session or a ready-resource session plan.
- Add or compose shots, optionally use references, prepare/review resource takes,
  then explicitly run them through the serial queue.
- Import and translate resources or legacy room libraries, inspect readiness,
  judge catalogue evidence, and use the Library and Slideshow views.

## Major Decisions

- Empty `composition_mode` preserves the legacy session path; `resource-v1`
  selects session-plan authoring without changing existing legacy sessions.
- Workflow mappings patch only mapped inputs; unmapped workflow values remain
  under the workflow's control.
- A session's look is shared across its shots while wardrobe is a default that
  individual takes may override. Shot rows are persisted before queueing, and
  the runner remains serial because the application targets one GPU.
- Machine-specific configuration, runtime data, generated images, and frontend
  build output stay outside the published source baseline.
