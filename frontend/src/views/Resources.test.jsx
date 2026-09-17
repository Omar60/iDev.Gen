// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import Resources from './Resources.jsx'
import { api } from '../api.js'
import actualPayloadFreeLibraries from './__fixtures__/actual_payload_free_libraries.json'

describe('Resources Component - Task 1.5 Specification & Contract Tests', () => {
  let container = null
  let root = null

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockResolvedValue([])
    vi.spyOn(api, 'post').mockResolvedValue({})
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue({})
    vi.spyOn(api, 'patch').mockResolvedValue({})
    vi.spyOn(api, 'del').mockResolvedValue({})
  })

  afterEach(() => {
    act(() => {
      root?.unmount()
    })
    container?.remove()
    container = null
  })

  const setInputValue = (input, value) => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }

  const setSelectValue = (select, value) => {
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  }

  const renderComponent = async (props = {}) => {
    await act(async () => {
      root.render(<Resources {...props} />)
    })
  }

  const switchToImportTab = async () => {
    const importTabBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent.includes('Import Preview')
    )
    expect(importTabBtn).toBeTruthy()
    await act(async () => {
      importTabBtn.click()
    })
  }

  const selectFiles = async (files) => {
    const fileInput = container.querySelector('input[type="file"]')
    expect(fileInput).toBeTruthy()
    Object.defineProperty(fileInput, 'files', {
      value: files,
      writable: true,
      configurable: true,
    })
    await act(async () => {
      fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }

  const clickUpload = async () => {
    const uploadBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent.trim().startsWith('Upload')
    )
    expect(uploadBtn).toBeTruthy()
    await act(async () => {
      uploadBtn.click()
    })
  }

  // 1. Selecting one browser file and multiple browser files
  it('1. handles selecting one browser file and multiple browser files', async () => {
    await renderComponent()
    await switchToImportTab()

    const fileInput = container.querySelector('input[type="file"]')
    expect(fileInput).toBeTruthy()
    expect(fileInput.getAttribute('multiple')).not.toBeNull()

    const file1 = new File(['{"invented": 1}'], 'character_alpha.json', { type: 'application/json' })
    await selectFiles([file1])

    expect(container.textContent).toContain('Selected: character_alpha.json')
    expect(container.textContent).toContain('Upload 1 file(s)')

    const file2 = new File(['{"invented": 2}'], 'character_beta.json', { type: 'application/json' })
    await selectFiles([file1, file2])

    expect(container.textContent).toContain('Selected: character_alpha.json, character_beta.json')
    expect(container.textContent).toContain('Upload 2 file(s)')
  })

  // 2. Create/upload using multipart and only SelectionView state
  it('2. creates selection and uploads files via multipart using SelectionView state', async () => {
    const createdSelection = {
      selection_id: 'sel-uuid-1',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const uploadedSelection = {
      selection_id: 'sel-uuid-1',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        {
          file_id: 'fid-101',
          file_name: 'character_alpha.json',
          byte_count: 128,
          declared_library: 'characters',
          effective_library_key: 'characters',
          matched_auxiliary_kinds: [],
          effective_auxiliary_kind: null,
          status: 'staged',
        },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return createdSelection
      return {}
    })

    let uploadedFormData = null
    const uploadMultipartSpy = vi.spyOn(api, 'uploadMultipart').mockImplementation(async (url, formData) => {
      if (url === '/api/resources/import-selections/sel-uuid-1/files') {
        uploadedFormData = formData
        return uploadedSelection
      }
      return {}
    })

    await renderComponent()
    await switchToImportTab()

    const file1 = new File(['{"invented": 1}'], 'character_alpha.json', { type: 'application/json' })
    await selectFiles([file1])
    await clickUpload()

    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections', expect.objectContaining({ request_id: expect.any(String) }))
    expect(uploadMultipartSpy).toHaveBeenCalled()
    expect(uploadedFormData.get('file')).toBeTruthy()
    expect(uploadedFormData.get('upload_id')).toBeTruthy()

    expect(container.textContent).toContain('character_alpha.json')
    expect(container.textContent).toContain('characters')
    expect(container.textContent).toContain('Revision: 1')
  })

  // 3. Valid declared identity with no typed path/key in the normal flow
  it('3. displays valid declared identity with no typed path or key needed', async () => {
    const selectionWithDeclared = {
      selection_id: 'sel-uuid-2',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        {
          file_id: 'fid-201',
          file_name: 'wardrobe_items.json',
          byte_count: 512,
          declared_library: 'wardrobe',
          effective_library_key: 'wardrobe',
          matched_auxiliary_kinds: [],
          effective_auxiliary_kind: null,
          status: 'staged',
        },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(selectionWithDeclared)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(selectionWithDeclared)

    await renderComponent()
    await switchToImportTab()

    const file = new File(['{}'], 'wardrobe_items.json', { type: 'application/json' })
    await selectFiles([file])
    await clickUpload()

    expect(container.textContent).toContain('wardrobe_items.json')
    expect(container.textContent).toContain('wardrobe')
    const stagedTableInputs = Array.from(container.querySelectorAll('table input[type="text"]'))
    expect(stagedTableInputs.length).toBe(0)
  })

  // 4. Out-of-order upload responses where the lower revision arrives last
  it('4. retains higher revision when lower revision arrives later (race condition)', async () => {
    let resolveReqA
    let resolveReqB
    const promiseA = new Promise((res) => { resolveReqA = res })
    const promiseB = new Promise((res) => { resolveReqB = res })

    const rev0 = {
      selection_id: 'sel-uuid-race',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }
    const rev1 = { ...rev0, selection_revision: 1, files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }] }
    const rev2 = { ...rev0, selection_revision: 2, files: [...rev1.files, { file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib2', effective_library_key: 'lib2', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }] }

    vi.spyOn(api, 'post').mockResolvedValue(rev0)
    let uploadCount = 0
    vi.spyOn(api, 'uploadMultipart').mockImplementation(() => {
      uploadCount++
      if (uploadCount === 1) return promiseA
      return promiseB
    })

    await renderComponent()
    await switchToImportTab()

    const f1 = new File(['1'], 'f1.json', { type: 'application/json' })
    const f2 = new File(['2'], 'f2.json', { type: 'application/json' })
    await selectFiles([f1, f2])

    const uploadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.trim().startsWith('Upload'))
    act(() => {
      uploadBtn.click()
    })

    // Response B (revision 2) arrives FIRST
    await act(async () => {
      resolveReqB(rev2)
    })
    expect(container.textContent).toContain('Revision: 2')
    expect(container.textContent).toContain('f2.json')

    // Response A (revision 1) arrives LATER
    await act(async () => {
      resolveReqA(rev1)
    })
    // Revision 2 is retained; stale Revision 1 is ignored
    expect(container.textContent).toContain('Revision: 2')
    expect(container.textContent).toContain('f2.json')
  })

  // 5. Stale/superseded preview cannot enable Import
  it('5. prevents stale preview from enabling Import', async () => {
    const viewRev1 = {
      selection_id: 'sel-preview',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const viewWithPreview = {
      ...viewRev1,
      preview: {
        preview_token: 'ptok-valid',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
    }

    const viewRev2 = {
      ...viewWithPreview,
      selection_revision: 2,
      preview: null,
      files: [...viewWithPreview.files, { file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib2', effective_library_key: 'lib2', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return viewRev1
      if (url.endsWith('/preview')) return viewWithPreview
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev1)

    await renderComponent()
    await switchToImportTab()

    // Initialize selection at rev 1
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    // Generate Preview -> returns viewWithPreview (committable: true)
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Preview Import'))
    expect(previewBtn).toBeTruthy()
    await act(async () => {
      previewBtn.click()
    })

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(false)

    // Upload another file -> server returns rev 2 with preview: null
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev2)
    await selectFiles([new File(['2'], 'f2.json', { type: 'application/json' })])
    await clickUpload()

    // Commit Import must now be disabled
    expect(commitBtn.disabled).toBe(true)
  })

  // 6. Target change invalidates the visible preview and disables Import
  it('6. target change invalidates preview and disables Import', async () => {
    const viewRev1 = {
      selection_id: 'sel-target-test',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: ['auxA', 'auxB'], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const viewWithPreview = {
      ...viewRev1,
      preview: {
        preview_token: 'ptok-valid-2',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
    }

    const viewAfterPatch = {
      ...viewWithPreview,
      selection_revision: 2,
      preview: null,
      files: [{ ...viewWithPreview.files[0], effective_auxiliary_kind: 'auxA' }],
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return viewRev1
      if (url.endsWith('/preview')) return viewWithPreview
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev1)
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue(viewAfterPatch)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    // Preview
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Preview Import'))
    expect(previewBtn).toBeTruthy()
    await act(async () => {
      previewBtn.click()
    })

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(false)

    // Open Advanced Compatibility
    const advBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Advanced Compatibility Options'))
    await act(async () => {
      advBtn.click()
    })

    const selectAux = container.querySelector('select')
    expect(selectAux).toBeTruthy()
    await act(async () => {
      setSelectValue(selectAux, 'auxA')
    })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Apply Choice'))
    await act(async () => {
      applyBtn.click()
    })

    expect(patchSpy).toHaveBeenCalledWith(
      '/api/resources/import-selections/sel-target-test/files/f1',
      { expected_revision: 1, effective_auxiliary_kind: 'auxA' }
    )

    // Preview invalidated -> Commit Import disabled
    expect(commitBtn.disabled).toBe(true)
  })

  // 7. Upload/remove invalidates the visible preview
  it('7. file remove invalidates visible preview and disables Import', async () => {
    const viewRev1 = {
      selection_id: 'sel-rm-test',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const viewWithPreview = {
      ...viewRev1,
      preview: {
        preview_token: 'ptok-rm',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
    }

    const viewAfterRemove = {
      ...viewWithPreview,
      selection_revision: 2,
      files: [],
      preview: null,
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return viewRev1
      if (url.endsWith('/preview')) return viewWithPreview
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev1)
    const delSpy = vi.spyOn(api, 'del').mockResolvedValue(viewAfterRemove)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    // Preview
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Preview Import'))
    expect(previewBtn).toBeTruthy()
    await act(async () => {
      previewBtn.click()
    })

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(false)

    // Remove file
    const removeBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.getAttribute('title') === 'Remove file from selection' || b.textContent.includes('✕')
    )
    expect(removeBtn).toBeTruthy()
    await act(async () => {
      removeBtn.click()
    })

    expect(delSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-rm-test/files/f1?expected_revision=1')
    expect(commitBtn.disabled).toBe(true)
  })

  // 8. Current revision is sent for preview, remove, PATCH, cancel, GET/status, and commit
  it('8. sends current revision with mutations, preview, commit, and cancel', async () => {
    const currentView = {
      selection_id: 'sel-rev-check',
      selection_revision: 7,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: {
        preview_token: 'ptok-rev-7',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
      commit_result: null,
      cleanup_warning: null,
    }

    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return currentView
      if (url.endsWith('/preview')) return currentView
      if (url.endsWith('/commit')) return { ...currentView, selection_revision: 8, state: 'committed' }
      if (url.endsWith('/cancel')) return { ...currentView, selection_revision: 8, state: 'cancelled' }
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(currentView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    // Preview check
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Preview Import'))
    expect(previewBtn).toBeTruthy()
    await act(async () => {
      previewBtn.click()
    })
    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-rev-check/preview', { expected_revision: 7 })

    // Commit check
    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    await act(async () => {
      commitBtn.click()
    })
    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-rev-check/commit', {
      expected_revision: 7,
      preview_token: 'ptok-rev-7',
    })
  })

  // 9. Committed, cancelled, expired, and committing states render correctly
  it('9. renders terminal and committing states with appropriate controls disabled', async () => {
    const openView = {
      selection_id: 'sel-term',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: {
        preview_token: 'ptok-term',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
      commit_result: null,
      cleanup_warning: null,
    }

    const committedView = {
      ...openView,
      selection_revision: 2,
      state: 'committed',
      commit_result: {
        version: 1,
        phase: 'commit',
        summary: {
          files: 1,
          inputs: 1,
          accepted: 1,
          auxiliary: 0,
          duplicates: 0,
          unresolved: 0,
          new: 1,
          unchanged: 0,
          updated: 0,
          missing: 0,
          recorded: 1,
          new_scene_revisions: 1,
          unchanged_scene_revisions: 0,
          updated_scene_revisions: 0,
          new_auxiliary_revisions: 0,
          unchanged_auxiliary_revisions: 0,
        },
        files: [
          {
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [
              {
                source_id: 's1',
                library_key: 'lib1',
                kind: 'rooms',
                classification: 'new',
                new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                previous_content_digest: null,
              },
            ],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          },
        ],
        missing_source_entries: [],
      },
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return openView
      if (url.endsWith('/commit')) return committedView
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    // Commit
    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn).toBeTruthy()
    await act(async () => {
      commitBtn.click()
    })

    expect(container.textContent).toContain('committed')
    expect(container.textContent).toContain('Resources successfully committed.')
    expect(container.textContent).toContain('Commit Report')
    expect(container.textContent).toContain('Recorded: 1')
    expect(container.querySelector('input[type="file"]').disabled).toBe(true)
  })

  // 10. cleanup_warning renders the sanitized public message and never exposes internal cleanup details
  it('10. renders sanitized cleanup_warning banner and never exposes internal cleanup details', async () => {
    const viewWithWarning = {
      selection_id: 'sel-warn',
      selection_revision: 2,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: 'Temporary cleanup is incomplete and will be retried automatically.',
    }

    vi.spyOn(api, 'post').mockResolvedValue(viewWithWarning)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewWithWarning)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).toContain('Temporary cleanup is incomplete and will be retried automatically.')
    expect(container.textContent).not.toContain('cleanup_state')
    expect(container.textContent).not.toContain('OSError')
  })

  // 11. Historical target compatibility with explicit Advanced choice
  it('11. provides historical target compatibility through Advanced choice', async () => {
    const view = {
      selection_id: 'sel-hist',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f-hist', file_name: 'historical_records.json', byte_count: 50, declared_library: 'legacy_rooms', effective_library_key: 'legacy_rooms', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(view)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(view)
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue({
      ...view,
      selection_revision: 2,
      files: [{ ...view.files[0], effective_library_key: 'custom_historical' }],
    })

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'historical_records.json', { type: 'application/json' })])
    await clickUpload()

    // Open Advanced
    const advBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Advanced Compatibility Options'))
    expect(advBtn).toBeTruthy()
    await act(async () => {
      advBtn.click()
    })

    const targetInput = container.querySelector('input[placeholder="Target library key"]')
    expect(targetInput).toBeTruthy()
    await act(async () => {
      setInputValue(targetInput, 'custom_historical')
    })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Apply Choice'))
    expect(applyBtn).toBeTruthy()
    await act(async () => {
      applyBtn.click()
    })

    expect(patchSpy).toHaveBeenCalledWith(
      '/api/resources/import-selections/sel-hist/files/f-hist',
      { expected_revision: 1, effective_library_key: 'custom_historical' }
    )
  })

  // 12. Ambiguous auxiliary candidates with explicit choice
  it('12. provides ambiguous auxiliary candidates with explicit choice', async () => {
    const view = {
      selection_id: 'sel-aux',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{
        file_id: 'f-aux',
        file_name: 'ambiguous_aux.json',
        byte_count: 50,
        declared_library: 'multi_aux',
        effective_library_key: 'multi_aux',
        matched_auxiliary_kinds: ['camera_preset', 'light_preset'],
        effective_auxiliary_kind: null,
        status: 'staged',
      }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(view)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(view)
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue({
      ...view,
      selection_revision: 2,
      files: [{ ...view.files[0], effective_auxiliary_kind: 'light_preset' }],
    })

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'ambiguous_aux.json', { type: 'application/json' })])
    await clickUpload()

    // Open Advanced
    const advBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Advanced Compatibility Options'))
    await act(async () => {
      advBtn.click()
    })

    const select = container.querySelector('select')
    expect(select).toBeTruthy()
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value)
    expect(options).toContain('camera_preset')
    expect(options).toContain('light_preset')

    await act(async () => {
      setSelectValue(select, 'light_preset')
    })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Apply Choice'))
    await act(async () => {
      applyBtn.click()
    })

    expect(patchSpy).toHaveBeenCalledWith(
      '/api/resources/import-selections/sel-aux/files/f-aux',
      { expected_revision: 1, effective_auxiliary_kind: 'light_preset' }
    )
  })

  // 13. Library search succeeds when every revision omits payload
  it('13. library search succeeds against payload-free revisions', async () => {
    const payloadFreeLibraries = [
      {
        library_key: 'wardrobe_sample',
        display_name: 'Wardrobe Sample Collection',
        kind: 'wardrobe',
        revisions: [
          {
            source_id: 'item-001',
            content_digest: 'digest-aaa',
            translation: { display_label: 'Silk Shirt' },
          },
          {
            source_id: 'item-002',
            content_digest: 'digest-bbb',
            translation: { display_label: 'Leather Jacket' },
          },
        ],
      },
    ]

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return payloadFreeLibraries
      if (url === '/api/models') return []
      return {}
    })

    await renderComponent()

    expect(container.textContent).toContain('Silk Shirt')
    expect(container.textContent).toContain('Leather Jacket')

    const searchInput = container.querySelector('input[placeholder="Search resources..."]')
    expect(searchInput).toBeTruthy()

    await act(async () => {
      setInputValue(searchInput, 'Silk')
    })

    expect(container.textContent).toContain('Silk Shirt')
    expect(container.textContent).not.toContain('Leather Jacket')
  })

  // 14. Search matches key, source ID, digest, and translated/display values
  it('14. matches search queries across key, source ID, content digest, and translated values', async () => {
    const fixture = [
      {
        library_key: 'invented_alpha_lib',
        display_name: 'Alpha Display',
        kind: 'character',
        revisions: [
          {
            source_id: 'src-specific-id',
            content_digest: 'digest-xyz789',
            translation: { title: 'Translated Hero' },
          },
        ],
      },
    ]

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return fixture
      return []
    })

    await renderComponent()
    const searchInput = container.querySelector('input[placeholder="Search resources..."]')

    // Test match by key
    await act(async () => {
      setInputValue(searchInput, 'invented_alpha')
    })
    expect(container.textContent).toContain('Translated Hero')

    // Test match by source_id
    await act(async () => {
      setInputValue(searchInput, 'src-specific')
    })
    expect(container.textContent).toContain('Translated Hero')

    // Test match by digest
    await act(async () => {
      setInputValue(searchInput, 'xyz789')
    })
    expect(container.textContent).toContain('Translated Hero')

    // Test match by translated value
    await act(async () => {
      setInputValue(searchInput, 'Hero')
    })
    expect(container.textContent).toContain('Translated Hero')

    // Test mismatch
    await act(async () => {
      setInputValue(searchInput, 'nonexistent_pattern')
    })
    expect(container.textContent).toContain('No resources match your filter.')
  })

  // 15. Missing raw payload never throws or causes a backend fetch
  it('15. missing payload never throws or triggers detail route during search', async () => {
    const throwingRevision = {
      source_id: 'src-throw-test',
      content_digest: 'digest-throw',
      translation: { label: 'Safe Label' },
    }
    Object.defineProperty(throwingRevision, 'payload', {
      get() {
        throw new Error('Explosion: payload accessed!')
      },
    })

    const lib = [
      {
        library_key: 'guard_lib',
        display_name: 'Guard Lib',
        kind: 'general',
        revisions: [throwingRevision],
      },
    ]

    const getSpy = vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return lib
      return []
    })

    await renderComponent()
    const searchInput = container.querySelector('input[placeholder="Search resources..."]')

    expect(() => {
      act(() => {
        setInputValue(searchInput, 'Safe')
      })
    }).not.toThrow()

    expect(container.textContent).toContain('Safe Label')
    const detailCalls = getSpy.mock.calls.filter(([url]) => url.includes('/api/resources/revisions/'))
    expect(detailCalls.length).toBe(0)
  })

  // 16. Legacy/Advanced path remains accessible
  it('16. keeps legacy path import section accessible and collapsible', async () => {
    await renderComponent()
    await switchToImportTab()

    expect(container.textContent).toContain('Legacy Path Import (Compatibility)')
    const toggleLegacyBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent.includes('Legacy Path Import (Compatibility)')
    )
    expect(toggleLegacyBtn).toBeTruthy()

    await act(async () => {
      toggleLegacyBtn.click()
    })

    expect(container.textContent).toContain('File path')
    expect(container.querySelector('input[placeholder="e.g. /path/to/source_library.json"]')).toBeTruthy()
  })

  // Privacy assertion
  it('strictly satisfies privacy constraints: no physical paths or private internals in DOM', async () => {
    const selectionView = {
      selection_id: 'sel-privacy-clean',
      selection_revision: 3,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        {
          file_id: 'fid-p1',
          file_name: 'safe_browser_upload.json',
          byte_count: 256,
          declared_library: 'characters',
          effective_library_key: 'characters',
          matched_auxiliary_kinds: [],
          effective_auxiliary_kind: null,
          status: 'staged',
        },
      ],
      preview: {
        preview_token: 'ptok-privacy-safe',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(selectionView)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(selectionView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'safe_browser_upload.json', { type: 'application/json' })])
    await clickUpload()

    const domText = container.innerHTML

    // Disallowed patterns and tokens
    expect(domText).not.toMatch(/[A-Za-z]:[\\/]+Users[\\/]+/i)
    expect(domText).not.toMatch(/\/(?:home|Users)\//i)
    expect(domText).not.toContain('staged_path')
    expect(domText).not.toContain('mtime_ns')
    expect(domText).not.toContain('raw_fingerprint')
    expect(domText).not.toContain('cleanup_state')
    expect(domText).not.toContain('attestation_token')
  })

  // =========================================================================
  // 13 Independent Review Adversarial Scenarios
  // =========================================================================

  // 1. Old preview response arriving after newer mutation does not re-enable Import
  it('adv 1. old preview response arriving after newer mutation does not re-enable Import', async () => {
    const baseViewRev1 = {
      selection_id: 'sel-race-1',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const previewResponseRev1 = {
      ...baseViewRev1,
      preview: {
        preview_token: 'ptok-old-rev1',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
    }

    const viewRev2AfterUpload = {
      ...baseViewRev1,
      selection_revision: 2,
      preview: null,
      files: [
        ...baseViewRev1.files,
        { file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
    }

    let resolvePreviewPromise
    const controlledPreviewPromise = new Promise((resolve) => {
      resolvePreviewPromise = resolve
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return baseViewRev1
      if (url.endsWith('/preview')) return controlledPreviewPromise
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockImplementation(async (url, formData) => {
      const file = formData.get('file')
      if (file && file.name === 'f1.json') {
        return baseViewRev1
      }
      return viewRev2AfterUpload
    })

    await renderComponent()
    await switchToImportTab()

    // 1. Initial selection at rev 1
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 1')

    // 2. Click Preview Import -> initiates preview request that remains pending
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Preview Import'))
    await act(async () => {
      previewBtn.click()
    })

    // 3. While preview is pending, upload another file -> completes and advances revision to 2 with preview: null
    await selectFiles([new File(['2'], 'f2.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 2')

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(true)

    // 4. Old preview promise now resolves with rev 1
    await act(async () => {
      resolvePreviewPromise(previewResponseRev1)
    })

    // 5. Must still be at Revision: 2 and Commit Import must remain disabled
    expect(container.textContent).toContain('Revision: 2')
    expect(commitBtn.disabled).toBe(true)
  })

  // 2. Reset / new selection while upload/preview was pending does not contaminate new state
  it('adv 2. reset while upload is in flight prevents old response from contaminating new state', async () => {
    let resolveUploadPromise
    const controlledUploadPromise = new Promise((resolve) => {
      resolveUploadPromise = resolve
    })

    const viewSel1Rev1 = {
      selection_id: 'sel-old-epoch',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const viewSel1Rev2 = {
      ...viewSel1Rev1,
      selection_revision: 2,
      files: [
        ...viewSel1Rev1.files,
        { file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
    }

    vi.spyOn(api, 'post').mockResolvedValue(viewSel1Rev1)
    vi.spyOn(api, 'uploadMultipart').mockImplementation(async (url, formData) => {
      const file = formData.get('file')
      if (file && file.name === 'f2.json') {
        return controlledUploadPromise
      }
      return viewSel1Rev1
    })

    await renderComponent()
    await switchToImportTab()

    // 1. Initial selection at rev 1
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('sel-old-epoch')

    // 2. Start upload of f2.json (hangs on controlledUploadPromise)
    await selectFiles([new File(['2'], 'f2.json', { type: 'application/json' })])
    const uploadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.trim().startsWith('Upload'))
    act(() => {
      uploadBtn.click()
    })

    // 3. User clicks New Selection (reset) while upload is pending
    const resetBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('New Selection'))
    expect(resetBtn).toBeTruthy()
    await act(async () => {
      resetBtn.click()
    })

    // Now in reset state
    expect(container.textContent).not.toContain('sel-old-epoch')

    // 4. Old upload now resolves
    await act(async () => {
      resolveUploadPromise(viewSel1Rev2)
    })

    // 5. The old selection must NOT be visible or re-anchored
    expect(container.textContent).not.toContain('sel-old-epoch')
    expect(container.textContent).not.toContain('Revision: 2')
  })

  // 3. New selection adopted after reset without being blocked by prior activeSelectionId
  it('adv 3. new selection adopted after reset without being blocked by prior activeSelectionId', async () => {
    const view1 = {
      selection_id: 'sel-first',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const view2 = {
      selection_id: 'sel-second',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib2', effective_library_key: 'lib2', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') {
        return { ...view1, selection_revision: 0, files: [] }
      }
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(view1)

    await renderComponent()
    await switchToImportTab()

    // 1. Initial selection
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('sel-first')

    // 2. Click New Selection
    const resetBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('New Selection'))
    expect(resetBtn).toBeTruthy()
    await act(async () => {
      resetBtn.click()
    })

    expect(container.textContent).not.toContain('sel-first')

    // 3. Now upload new file for second selection
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') {
        return { ...view2, selection_revision: 0, files: [] }
      }
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(view2)

    await selectFiles([new File(['2'], 'f2.json', { type: 'application/json' })])
    await clickUpload()

    // 4. Must adopt sel-second cleanly!
    expect(container.textContent).toContain('sel-second')
    expect(container.textContent).toContain('f2.json')
  })

  // 4. Real cancel
  it('adv 4. cancel selection updates state to cancelled and disables controls', async () => {
    const openView = {
      selection_id: 'sel-cancel-test',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const cancelledView = {
      ...openView,
      selection_revision: 2,
      state: 'cancelled',
    }

    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return openView
      if (url.endsWith('/cancel')) return cancelledView
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    const cancelBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Cancel Selection'))
    expect(cancelBtn).toBeTruthy()
    await act(async () => {
      cancelBtn.click()
    })

    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-cancel-test/cancel', {
      expected_revision: 1,
    })
    expect(container.textContent).toContain('cancelled')
    expect(container.textContent).toContain('Selection cancelled.')
    expect(container.querySelector('input[type="file"]').disabled).toBe(true)
  })

  // 5. Real GET/status updates selection when server reports higher revision
  it('adv 5. status check updates selection when higher revision is returned', async () => {
    const openView = {
      selection_id: 'sel-status-test',
      selection_revision: 1,
      state: 'committing',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const committedView = {
      ...openView,
      selection_revision: 3,
      commit_result: {
        version: 1,
        phase: 'commit',
        summary: {
          files: 1,
          inputs: 1,
          accepted: 1,
          auxiliary: 0,
          duplicates: 0,
          unresolved: 0,
          new: 1,
          unchanged: 0,
          updated: 0,
          missing: 0,
          recorded: 1,
          new_scene_revisions: 1,
          unchanged_scene_revisions: 0,
          updated_scene_revisions: 0,
          new_auxiliary_revisions: 0,
          unchanged_auxiliary_revisions: 0,
        },
        files: [
          {
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [
              {
                source_id: 's1',
                library_key: 'lib1',
                kind: 'rooms',
                classification: 'new',
                new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                previous_content_digest: null,
              },
            ],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          },
        ],
        missing_source_entries: [],
      },
      state: 'committed',
    }

    vi.spyOn(api, 'post').mockResolvedValue(openView)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)
    const getSpy = vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url.includes('/import-selections/sel-status-test')) return committedView
      return []
    })

    vi.useFakeTimers()
    try {
      await renderComponent()
      await switchToImportTab()

      await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
      await clickUpload()

      // Fast forward polling interval
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500)
      })

      expect(getSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-status-test')
      expect(container.textContent).toContain('committed')
      expect(container.textContent).toContain('Import committed successfully.')
    } finally {
      vi.useRealTimers()
    }
  })

  // 6. Committed state renders commit report and disables controls
  it('adv 6. renders committed state with report and disabled file selection', async () => {
    const committedView = {
      selection_id: 'sel-committed-test',
      selection_revision: 4,
      state: 'committed',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: {
        version: 1,
        phase: 'commit',
        summary: {
          files: 1,
          inputs: 2,
          accepted: 2,
          auxiliary: 0,
          duplicates: 0,
          unresolved: 0,
          new: 2,
          unchanged: 0,
          updated: 0,
          missing: 0,
          recorded: 2,
          new_scene_revisions: 2,
          unchanged_scene_revisions: 0,
          updated_scene_revisions: 0,
          new_auxiliary_revisions: 0,
          unchanged_auxiliary_revisions: 0,
        },
        files: [
          {
            library_key: 'lib1',
            total_inputs: 2,
            accepted: [
              {
                source_id: 's1',
                library_key: 'lib1',
                kind: 'rooms',
                classification: 'new',
                new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                previous_content_digest: null,
              },
              {
                source_id: 's2',
                library_key: 'lib1',
                kind: 'rooms',
                classification: 'new',
                new_content_digest: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
                previous_content_digest: null,
              },
            ],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          },
        ],
        missing_source_entries: [],
      },
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(committedView)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(committedView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).toContain('committed')
    expect(container.textContent).toContain('Resources successfully committed.')
    expect(container.textContent).toContain('Recorded: 2')
    expect(container.querySelector('input[type="file"]').disabled).toBe(true)
  })

  // 7. Cancelled state renders disabled controls
  it('adv 7. renders cancelled state with disabled controls and notices', async () => {
    const cancelledView = {
      selection_id: 'sel-cancelled-state',
      selection_revision: 3,
      state: 'cancelled',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(cancelledView)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(cancelledView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).toContain('cancelled')
    expect(container.textContent).toContain('Selection cancelled. Mutation and import are disabled.')
    expect(container.querySelector('input[type="file"]').disabled).toBe(true)
  })

  // 8. Expired state renders expired notification and disabled controls
  it('adv 8. renders expired state with notification and disabled controls', async () => {
    const expiredView = {
      selection_id: 'sel-expired-state',
      selection_revision: 2,
      state: 'expired',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(expiredView)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(expiredView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).toContain('expired')
    expect(container.textContent).toContain('Selection expired. Start a new selection to import resources.')
    expect(container.querySelector('input[type="file"]').disabled).toBe(true)
  })

  // 9. HTTP 202 committing followed by status committed avoids premature success and reloads libraries on committed
  it('adv 9. handles HTTP 202 committing without premature success, then transitions to committed via polling', async () => {
    const openViewWithPreview = {
      selection_id: 'sel-202-test',
      selection_revision: 2,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: {
        preview_token: 'ptok-202',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
      commit_result: null,
      cleanup_warning: null,
    }

    const committingView = {
      ...openViewWithPreview,
      selection_revision: 3,
      state: 'committing',
    }

    const committedView = {
      ...committingView,
      selection_revision: 4,
      state: 'committed',
      commit_result: {
        version: 1,
        phase: 'commit',
        summary: {
          files: 1,
          inputs: 1,
          accepted: 1,
          auxiliary: 0,
          duplicates: 0,
          unresolved: 0,
          new: 1,
          unchanged: 0,
          updated: 0,
          missing: 0,
          recorded: 1,
          new_scene_revisions: 1,
          unchanged_scene_revisions: 0,
          updated_scene_revisions: 0,
          new_auxiliary_revisions: 0,
          unchanged_auxiliary_revisions: 0,
        },
        files: [
          {
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [
              {
                source_id: 's1',
                library_key: 'lib1',
                kind: 'rooms',
                classification: 'new',
                new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                previous_content_digest: null,
              },
            ],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          },
        ],
        missing_source_entries: [],
      },
    }

    let librariesReloaded = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        librariesReloaded++
        return []
      }
      if (url.includes('/import-selections/sel-202-test')) {
        return committedView
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return openViewWithPreview
      if (url.endsWith('/commit')) return committingView // 202 accepted
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openViewWithPreview)

    vi.useFakeTimers()
    try {
      await renderComponent()
      await switchToImportTab()

      await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
      await clickUpload()

      const initialLibraryLoads = librariesReloaded

      // Click Commit Import
      const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
      expect(commitBtn.disabled).toBe(false)

      await act(async () => {
        commitBtn.click()
      })

      // Must show intermediate notice, NOT final success
      expect(container.textContent).toContain('Commit is actively processing')
      expect(container.textContent).not.toContain('Import committed successfully')
      expect(container.textContent).not.toContain('Resources successfully committed')
      expect(librariesReloaded).toBe(initialLibraryLoads) // libraries NOT reloaded yet

      // Advance polling interval
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500)
      })

      // Polling followed through to committed!
      expect(container.textContent).toContain('Import committed successfully.')
      expect(librariesReloaded).toBeGreaterThan(initialLibraryLoads) // libraries reloaded now!
    } finally {
      vi.useRealTimers()
    }
  })

  // 10. detail.current with higher revision is adopted on error
  it('adv 10. adopts detail.current with higher revision when action fails with 409', async () => {
    const viewRev1 = {
      selection_id: 'sel-detail-test',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const higherCurrent = {
      ...viewRev1,
      selection_revision: 5,
      files: [
        ...viewRev1.files,
        { file_id: 'f2', file_name: 'f2.json', byte_count: 20, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
    }

    vi.spyOn(api, 'post').mockResolvedValue(viewRev1)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev1)
    vi.spyOn(api, 'del').mockRejectedValue({
      message: 'Revision conflict: expected 1 but got 5',
      detail: { current: higherCurrent },
    })

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 1')

    // Click remove file -> fails with 409 returning revision 5
    const removeBtn = Array.from(container.querySelectorAll('button')).find((b) => b.getAttribute('title') === 'Remove file from selection' || b.textContent.includes('✕'))
    await act(async () => {
      removeBtn.click()
    })

    // Higher revision must be adopted
    expect(container.textContent).toContain('Revision: 5')
    expect(container.textContent).toContain('f2.json')
  })

  // 11. detail.current with lower revision is ignored on error
  it('adv 11. ignores detail.current with lower revision when action fails with error', async () => {
    const viewRev4 = {
      selection_id: 'sel-detail-low',
      selection_revision: 4,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const lowerCurrent = {
      ...viewRev4,
      selection_revision: 2,
    }

    vi.spyOn(api, 'post').mockResolvedValue(viewRev4)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewRev4)
    vi.spyOn(api, 'del').mockRejectedValue({
      message: 'Stale error dump',
      detail: { current: lowerCurrent },
    })

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 4')

    const removeBtn = Array.from(container.querySelectorAll('button')).find((b) => b.getAttribute('title') === 'Remove file from selection' || b.textContent.includes('✕'))
    await act(async () => {
      removeBtn.click()
    })

    // Revision 4 remains
    expect(container.textContent).toContain('Revision: 4')
  })

  // 12. Simulated private fields and getters are stripped and never present in React state or DOM
  it('adv 12. simulated private fields and getters are never accessed or present in React state', async () => {
    const viewWithGetters = {
      selection_id: 'sel-safe-getter',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'safe.json', byte_count: 50, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    Object.defineProperty(viewWithGetters, 'staged_path', {
      get() {
        throw new Error('Explosion: staged_path accessed!')
      },
    })
    Object.defineProperty(viewWithGetters, 'cleanup_state', {
      get() {
        throw new Error('Explosion: cleanup_state accessed!')
      },
    })
    Object.defineProperty(viewWithGetters, 'raw_payload', {
      get() {
        throw new Error('Explosion: raw_payload accessed!')
      },
    })

    vi.spyOn(api, 'post').mockResolvedValue(viewWithGetters)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewWithGetters)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'safe.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).toContain('safe.json')
    expect(container.innerHTML).not.toContain('staged_path')
    expect(container.innerHTML).not.toContain('cleanup_state')
    expect(container.innerHTML).not.toContain('raw_payload')
  })

  // 13. Incomplete preview / committable without token / without digest / without report disables Import
  it('adv 13. incomplete preview disables Import button across all missing elements', async () => {
    const makeViewWithPreview = (previewPatch) => ({
      selection_id: 'sel-incomp-preview',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: {
        preview_token: 'ptok-valid-token',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
        ...previewPatch,
      },
      commit_result: null,
      cleanup_warning: null,
    })

    // Subcase A: empty/missing preview_token
    const viewNoToken = makeViewWithPreview({ preview_token: '' })
    vi.spyOn(api, 'post').mockResolvedValue(viewNoToken)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewNoToken)

    await renderComponent()
    await switchToImportTab()
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(true)

    // Subcase B: non-hex or too short manifest_digest
    const viewBadDigest = makeViewWithPreview({ manifest_digest: 'not-a-valid-hex-digest' })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewBadDigest)
    await selectFiles([new File(['2'], 'f2.json', { type: 'application/json' })])
    await clickUpload()
    expect(commitBtn.disabled).toBe(true)

    // Subcase C: missing report
    const viewNoReport = makeViewWithPreview({ report: null })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewNoReport)
    await selectFiles([new File(['3'], 'f3.json', { type: 'application/json' })])
    await clickUpload()
    expect(commitBtn.disabled).toBe(true)

    // Subcase D: committable is false
    const viewNotCommittable = makeViewWithPreview({ committable: false })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(viewNotCommittable)
    await selectFiles([new File(['4'], 'f4.json', { type: 'application/json' })])
    await clickUpload()
    expect(commitBtn.disabled).toBe(true)
  })

  it('rejects canonical-invalid preview.report without rendering a synthetic zero-count preview', async () => {
    const createdSelection = {
      selection_id: 'sel-invalid-preview',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }
    const invalidPreviewSelection = {
      ...createdSelection,
      selection_revision: 1,
      files: [{
        file_id: 'f1',
        file_name: 'invalid-preview.json',
        byte_count: 10,
        declared_library: 'lib1',
        effective_library_key: 'lib1',
        matched_auxiliary_kinds: [],
        effective_auxiliary_kind: null,
        status: 'staged',
      }],
      preview: {
        preview_token: 'ptok-invalid-preview',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: { phase: 'preview' },
      },
    }

    vi.spyOn(api, 'post').mockResolvedValue(createdSelection)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(invalidPreviewSelection)

    await renderComponent()
    await switchToImportTab()
    await selectFiles([new File(['invalid'], 'invalid-preview.json', { type: 'application/json' })])
    await clickUpload()

    expect(container.textContent).not.toContain('Import Preview Results')
    expect(container.textContent).not.toContain('New: 0')
    expect(container.textContent).not.toContain('Accepted: 0')
    const commitButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent.includes('Commit Import')
    )
    expect(commitButton).toBeTruthy()
    expect(commitButton.disabled).toBe(true)
  })

  // =========================================================================
  // Task 1.6 - Integration & Contract Suite (Section 3.2)
  // =========================================================================

  // 1. Full flow guided by real contract: create -> upload -> target/auxiliary -> preview -> commit
  it('1.6-1. executes full flow: create -> upload -> target/auxiliary -> preview -> commit with exact SelectionView shapes', async () => {
    const sel0 = {
      selection_id: 'sel-flow-101',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const sel1 = {
      selection_id: 'sel-flow-101',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        {
          file_id: 'fid-101',
          file_name: 'scenes.json',
          byte_count: 240,
          declared_library: 'fused_scenes',
          effective_library_key: 'fused_scenes',
          matched_auxiliary_kinds: ['mined_labels'],
          effective_auxiliary_kind: null,
          status: 'staged',
        },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const sel2 = {
      ...sel1,
      selection_revision: 2,
      files: [
        {
          ...sel1.files[0],
          effective_library_key: 'scenes_v2',
          effective_auxiliary_kind: 'mined_labels',
        },
      ],
    }

    const selPreview = {
      ...sel2,
      selection_revision: 2,
      preview: {
        preview_token: 'ptok-flow-valid-hex-token-1234',
        manifest_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 1,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [
            {
              library_key: 'scenes_v2',
              total_inputs: 1,
              accepted: [
                {
                  library_key: 'scenes_v2',
                  source_id: 'scene_flow_1',
                  kind: 'fused_scenes',
                  classification: 'new',
                  new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                  previous_content_digest: null,
                },
              ],
              auxiliary: [
                {
                  library_key: 'scenes_v2',
                  kind: 'mined_labels',
                  classification: 'new',
                  new_content_digest: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
                  previous_content_digest: null,
                },
              ],
              unresolved: [],
              duplicates: [],
            },
          ],
          missing_source_entries: [],
        },
      },
    }

    const selCommitted = {
      ...selPreview,
      selection_revision: 3,
      state: 'committed',
      preview: null,
      commit_result: {
        version: 1,
        phase: 'commit',
        summary: {
          files: 1,
          inputs: 2,
          accepted: 1,
          auxiliary: 1,
          duplicates: 0,
          unresolved: 0,
          new: 1,
          unchanged: 0,
          updated: 0,
          missing: 0,
          recorded: 2,
          new_scene_revisions: 1,
          unchanged_scene_revisions: 0,
          updated_scene_revisions: 0,
          new_auxiliary_revisions: 1,
          unchanged_auxiliary_revisions: 0,
        },
        files: [
          {
            library_key: 'scenes_v2',
            total_inputs: 2,
            accepted: [
              {
                source_id: 'scene_flow_1',
                library_key: 'scenes_v2',
                kind: 'fused_scenes',
                classification: 'new',
                new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                previous_content_digest: null,
              },
            ],
            auxiliary: [
              {
                library_key: 'scenes_v2',
                kind: 'mined_labels',
                classification: 'new',
                new_content_digest: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
                previous_content_digest: null,
              },
            ],
            duplicates: [],
            unresolved: [],
          },
        ],
        missing_source_entries: [],
      },
    }

    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return sel0
      if (url === '/api/resources/import-selections/sel-flow-101/preview') return selPreview
      if (url === '/api/resources/import-selections/sel-flow-101/commit') return selCommitted
      return {}
    })

    const uploadSpy = vi.spyOn(api, 'uploadMultipart').mockResolvedValue(sel1)
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue(sel2)

    await renderComponent()
    await switchToImportTab()

    // Step 1: Upload file
    const file = new File(['{"items":[]}'], 'scenes.json', { type: 'application/json' })
    await selectFiles([file])
    await clickUpload()

    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections', expect.objectContaining({ request_id: expect.any(String) }))
    expect(uploadSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-flow-101/files', expect.any(FormData))
    expect(container.textContent).toContain('Revision: 1')
    expect(container.textContent).toContain('scenes.json')

    // Step 2: Open advanced options and patch target / auxiliary
    const advBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Show Advanced Compatibility Options'))
    expect(advBtn).toBeTruthy()
    await act(async () => {
      advBtn.click()
    })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Apply Choice')
    expect(applyBtn).toBeTruthy()
    const auxSelect = Array.from(container.querySelectorAll('select')).find((s) => s.innerHTML.includes('mined_labels'))
    expect(auxSelect).toBeTruthy()
    setSelectValue(auxSelect, 'mined_labels')

    await act(async () => {
      applyBtn.click()
    })
    expect(patchSpy).toHaveBeenCalledWith(
      '/api/resources/import-selections/sel-flow-101/files/fid-101',
      expect.objectContaining({ expected_revision: 1, effective_auxiliary_kind: 'mined_labels' })
    )
    expect(container.textContent).toContain('Revision: 2')

    // Step 3: Run preview
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Import')
    expect(previewBtn).toBeTruthy()
    await act(async () => {
      previewBtn.click()
    })
    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-flow-101/preview', { expected_revision: 2 })

    // Step 4: Commit import
    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(false)
    await act(async () => {
      commitBtn.click()
      await Promise.resolve()
    })
    expect(postSpy).toHaveBeenCalledWith('/api/resources/import-selections/sel-flow-101/commit', {
      expected_revision: 2,
      preview_token: 'ptok-flow-valid-hex-token-1234',
    })

    // Step 5: Verify committed state
    expect(container.textContent).toContain('committed')
    expect(container.textContent).toContain('Revision: 3')
    expect(container.textContent).toContain('Import committed successfully')

    // Verify absence of any private fields in DOM
    const html = container.innerHTML
    expect(html).not.toContain('staged_path')
    expect(html).not.toContain('cleanup_state')
    expect(html).not.toContain('storage_path')
    expect(html).not.toContain('source_digest')
  })

  // 2. Out-of-order responses: if response with revision 3 arrives after revision 4, view retains revision 4
  it('1.6-2. out-of-order responses: view retains revision 4 when revision 3 arrives later', async () => {
    let resolveUpload1, resolveUpload2
    const uploadPromise1 = new Promise((res) => { resolveUpload1 = res })
    const uploadPromise2 = new Promise((res) => { resolveUpload2 = res })

    const sel0 = {
      selection_id: 'sel-ooo-1',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(sel0)
    let callCount = 0
    vi.spyOn(api, 'uploadMultipart').mockImplementation(() => {
      callCount++
      if (callCount === 1) return uploadPromise1
      return uploadPromise2
    })

    await renderComponent()
    await switchToImportTab()

    const f1 = new File(['1'], 'f1.json', { type: 'application/json' })
    const f2 = new File(['2'], 'f2.json', { type: 'application/json' })
    await selectFiles([f1, f2])

    const uploadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Upload 2 file(s)'))
    act(() => {
      uploadBtn.click()
    })

    const viewRev4 = {
      selection_id: 'sel-ooo-1',
      selection_revision: 4,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'f2', file_name: 'f2.json', byte_count: 1, declared_library: 'lib', effective_library_key: 'lib', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    await act(async () => {
      resolveUpload2(viewRev4)
    })
    expect(container.textContent).toContain('Revision: 4')

    const viewRev3 = {
      selection_id: 'sel-ooo-1',
      selection_revision: 3,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'f1', file_name: 'f1.json', byte_count: 1, declared_library: 'lib', effective_library_key: 'lib', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    await act(async () => {
      resolveUpload1(viewRev3)
    })

    expect(container.textContent).toContain('Revision: 4')
    expect(container.textContent).not.toContain('Revision: 3')
  })

  // 3. Out-of-order promises: request A starting before request B but resolving after adopts superior revision (rev 4 > rev 3)
  it('1.6-3. out-of-order promises: request A starting before request B but resolving after adopts superior revision (rev 4 > rev 3)', async () => {
    let resolveUploadA, resolveUploadB
    const promiseA = new Promise((res) => { resolveUploadA = res })
    const promiseB = new Promise((res) => { resolveUploadB = res })

    const selInit = {
      selection_id: 'sel-prom-1',
      selection_revision: 0,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(selInit)

    let uploadCallIndex = 0
    vi.spyOn(api, 'uploadMultipart').mockImplementation(() => {
      uploadCallIndex++
      if (uploadCallIndex === 1) return promiseA // Request A started first
      return promiseB                           // Request B started second
    })

    await renderComponent()
    await switchToImportTab()

    const fA = new File(['A'], 'fileA.json', { type: 'application/json' })
    const fB = new File(['B'], 'fileB.json', { type: 'application/json' })
    await selectFiles([fA, fB])

    // Trigger concurrent upload of both files
    const uploadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Upload 2 file(s)'))
    act(() => {
      uploadBtn.click()
    })

    // Request B (started second) finishes first with revision 3
    const rev3View = {
      selection_id: 'sel-prom-1',
      selection_revision: 3,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'fB', file_name: 'fileB.json', byte_count: 1, declared_library: 'libB', effective_library_key: 'libB', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }
    await act(async () => {
      resolveUploadB(rev3View)
    })
    expect(container.textContent).toContain('Revision: 3')
    expect(container.textContent).toContain('fileB.json')

    // Request A (started first) finishes later with superior revision 4
    const rev4View = {
      selection_id: 'sel-prom-1',
      selection_revision: 4,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'fA', file_name: 'fileA.json', byte_count: 1, declared_library: 'libA', effective_library_key: 'libA', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }
    await act(async () => {
      resolveUploadA(rev4View)
    })

    // View MUST adopt revision 4 and fileA.json because revision 4 > revision 3
    expect(container.textContent).toContain('Revision: 4')
    expect(container.textContent).toContain('fileA.json')
    expect(container.textContent).not.toContain('Revision: 3')
  })

  // 4. Selection epoch fencing: responses belonging to cancelled/replaced selection are ignored
  it('1.6-4. selection epoch fencing: responses from old/replaced selection are ignored and do not paint old data', async () => {
    let resolveOldUpload
    const oldUploadPromise = new Promise((res) => { resolveOldUpload = res })

    const selOld1 = {
      selection_id: 'sel-epoch-old',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [{ file_id: 'f0', file_name: 'f0.json', byte_count: 10, declared_library: 'lib_old', effective_library_key: 'lib_old', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(selOld1)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValueOnce(selOld1)

    await renderComponent()
    await switchToImportTab()
    await selectFiles([new File(['0'], 'f0.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('sel-epoch-old')

    // Now selection 1 is active and "New Selection" button is in DOM
    vi.spyOn(api, 'uploadMultipart').mockReturnValueOnce(oldUploadPromise)
    await selectFiles([new File(['old'], 'old_secret_file.json', { type: 'application/json' })])
    act(() => {
      const uploadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Upload'))
      uploadBtn.click()
    })

    // User resets via New Selection button -> increments epoch
    const newSelBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('New Selection'))
    expect(newSelBtn).toBeTruthy()
    await act(async () => {
      newSelBtn.click()
    })

    // Now resolve old upload with sel-epoch-old
    const oldResolvedView = {
      selection_id: 'sel-epoch-old',
      selection_revision: 5,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'f-old', file_name: 'old_secret_file.json', byte_count: 50, declared_library: 'lib_old', effective_library_key: 'lib_old', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    await act(async () => {
      resolveOldUpload(oldResolvedView)
    })

    // Old selection data must NOT be painted!
    expect(container.textContent).not.toContain('old_secret_file.json')
    expect(container.textContent).not.toContain('sel-epoch-old')
  })

  // 5. Stale preview arriving after mutation leaves Import disabled
  it('1.6-5. stale preview arriving after mutation is discarded and leaves Import disabled', async () => {
    let resolvePreview
    const previewPromise = new Promise((res) => { resolvePreview = res })

    const selView1 = {
      selection_id: 'sel-stale-prev',
      selection_revision: 1,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'fid-1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const selView2 = {
      ...selView1,
      selection_revision: 2,
      files: [],
    }

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return selView1
      if (url === '/api/resources/import-selections/sel-stale-prev/preview') return previewPromise
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(selView1)
    vi.spyOn(api, 'del').mockResolvedValue(selView2)

    await renderComponent()
    await switchToImportTab()
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 1')

    // Click Preview Import
    const prevBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Import')
    act(() => {
      prevBtn.click()
    })

    // User removes file (mutates selection to revision 2)
    const removeBtn = container.querySelector('button[title="Remove file from selection"]')
    expect(removeBtn).toBeTruthy()
    await act(async () => {
      removeBtn.click()
    })
    expect(container.textContent).toContain('Revision: 2')

    // Stale preview finishes for revision 1
    const stalePreviewView = {
      ...selView1,
      selection_revision: 1,
      preview: {
        preview_token: 'ptok-stale-token',
        manifest_digest: '0123456789abcdef0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
      },
    }

    await act(async () => {
      resolvePreview(stalePreviewView)
    })

    expect(container.textContent).toContain('Revision: 2')
    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn.disabled).toBe(true)
  })

  // 6. 202 Committing polling: transition from 202 committing to 200/committed without premature reload
  it('1.6-6. orderly 202 committing polling transition to committed without premature reload of libraries', async () => {
    vi.useFakeTimers()
    try {
      const selOpen = {
        selection_id: 'sel-poll-1',
        selection_revision: 1,
        state: 'open',
        expires_at: '2026-09-17T20:00:00Z',
        files: [
          { file_id: 'fid-1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
        ],
        preview: {
          preview_token: 'ptok-poll-token',
          manifest_digest: '0123456789abcdef0123456789abcdef',
          committable: true,
          report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [{
            library_key: 'lib1',
            total_inputs: 1,
            accepted: [{
              source_id: 's1',
              library_key: 'lib1',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        },
        },
        commit_result: null,
        cleanup_warning: null,
      }

      const selCommitting = {
        ...selOpen,
        selection_revision: 2,
        state: 'committing',
      }

      const selCommitted = {
        ...selOpen,
        selection_revision: 2,
        state: 'committed',
        commit_result: {
          version: 1,
          phase: 'commit',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
            recorded: 1,
            new_scene_revisions: 1,
            unchanged_scene_revisions: 0,
            updated_scene_revisions: 0,
            new_auxiliary_revisions: 0,
            unchanged_auxiliary_revisions: 0,
          },
          files: [
            {
              library_key: 'lib1',
              total_inputs: 1,
              accepted: [
                {
                  source_id: 's1',
                  library_key: 'lib1',
                  kind: 'rooms',
                  classification: 'new',
                  new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                  previous_content_digest: null,
                },
              ],
              auxiliary: [],
              duplicates: [],
              unresolved: [],
            },
          ],
          missing_source_entries: [],
        },
      }

      let libFetchCount = 0
      let getSelectionPollCount = 0

      vi.spyOn(api, 'get').mockImplementation(async (url) => {
        if (url === '/api/resources/libraries') {
          libFetchCount++
          return []
        }
        if (url === '/api/resources/import-selections/sel-poll-1') {
          getSelectionPollCount++
          if (getSelectionPollCount === 1) return selCommitting
          return selCommitted
        }
        return []
      })

      vi.spyOn(api, 'post').mockImplementation(async (url) => {
        if (url === '/api/resources/import-selections') return selOpen
        if (url === '/api/resources/import-selections/sel-poll-1/commit') return selCommitting
        return {}
      })
      vi.spyOn(api, 'uploadMultipart').mockResolvedValue(selOpen)

      await renderComponent()
      await switchToImportTab()
      await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
      await clickUpload()

      const initialLibFetches = libFetchCount

      // Click Commit Import -> receives 202 committing
      const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
      await act(async () => {
        commitBtn.click()
      })

      expect(container.textContent).toContain('committing')
      // Libraries must NOT have reloaded yet!
      expect(libFetchCount).toBe(initialLibFetches)

      // Advance timer for first poll (2000ms) -> still committing
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      expect(getSelectionPollCount).toBe(1)
      expect(libFetchCount).toBe(initialLibFetches)

      // Advance timer for second poll (2000ms) -> becomes committed
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      expect(getSelectionPollCount).toBe(2)
      expect(container.textContent).toContain('committed')
      // Now libraries must be reloaded!
      expect(libFetchCount).toBe(initialLibFetches + 1)
      expect(container.textContent).toContain('Import committed successfully')
    } finally {
      vi.useRealTimers()
    }
  })

  // 7. detail.current adoption: adopts higher revision; rejects lower/equal
  it('1.6-7. detail.current adoption: adopts current view on 409 conflict if revision is higher; rejects if lower/equal', async () => {
    const selInit = {
      selection_id: 'sel-det-1',
      selection_revision: 2,
      state: 'open',
      expires_at: '2026-09-17T20:00:00Z',
      files: [
        { file_id: 'fid-1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
      ],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    vi.spyOn(api, 'post').mockResolvedValue(selInit)
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(selInit)

    await renderComponent()
    await switchToImportTab()
    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()
    expect(container.textContent).toContain('Revision: 2')

    // Case A: 409 with higher revision 5
    const conflictHigher = new Error('Conflict')
    conflictHigher.detail = {
      current: {
        ...selInit,
        selection_revision: 5,
        files: [
          { file_id: 'fid-1', file_name: 'f1.json', byte_count: 10, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
          { file_id: 'fid-2', file_name: 'f2_server_added.json', byte_count: 20, declared_library: 'lib1', effective_library_key: 'lib1', matched_auxiliary_kinds: [], effective_auxiliary_kind: null, status: 'staged' },
        ],
      },
    }

    vi.spyOn(api, 'patch').mockRejectedValueOnce(conflictHigher)

    const advBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Show Advanced'))
    await act(async () => { advBtn.click() })
    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Apply Choice')
    const input = container.querySelector('input[placeholder="Target library key"]')
    setInputValue(input, 'target_new')

    await act(async () => {
      applyBtn.click()
      await Promise.resolve()
    })

    // Adopts revision 5 and f2_server_added.json
    expect(container.textContent).toContain('Revision: 5')
    expect(container.textContent).toContain('f2_server_added.json')

    // Case B: 409 with lower revision 3 (when current is 5)
    const conflictLower = new Error('Conflict')
    conflictLower.detail = {
      current: {
        ...selInit,
        selection_revision: 3,
        files: [],
      },
    }

    vi.spyOn(api, 'patch').mockRejectedValueOnce(conflictLower)
    setInputValue(input, 'target_new_2')
    await act(async () => {
      applyBtn.click()
      await Promise.resolve()
    })

    // Must retain revision 5, not downgrade to 3
    expect(container.textContent).toContain('Revision: 5')
    expect(container.textContent).toContain('f2_server_added.json')
  })

  // 8. Resource Browser search against actual_payload_free_libraries fixture
  it('1.6-8. Resource Browser search against actual_payload_free_libraries fixture operates payload-free without detail fetches', async () => {
    const getSpy = vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return actualPayloadFreeLibraries
      if (url === '/api/models') return []
      if (url.startsWith('/api/resources/revisions/')) {
        throw new Error('UNEXPECTED_DETAIL_FETCH: browser search must not fetch revision details')
      }
      return []
    })

    await renderComponent()

    expect(container.textContent).toContain('Studio Gallery Rooms')
    expect(container.textContent).toContain('Summer Couture Scenes')

    const searchInput = container.querySelector('input[placeholder="Search resources..."]')
    expect(searchInput).toBeTruthy()

    // 1. Search by library_key: 'rooms_studio_gallery'
    await act(async () => {
      setInputValue(searchInput, 'rooms_studio_gallery')
    })
    expect(container.textContent).toContain('Studio Gallery Rooms')
    expect(container.textContent).not.toContain('Summer Couture Scenes')

    // 2. Search by source_id: 'room_grand_loft'
    await act(async () => {
      setInputValue(searchInput, 'room_grand_loft')
    })
    expect(container.textContent).toContain('Grand Sunlight Loft')
    expect(container.textContent).toContain('room_grand_loft')
    expect(container.textContent).not.toContain('Summer Couture Scenes')

    // 3. Search by content_digest: exact digest from actualPayloadFreeLibraries fixture
    await act(async () => {
      setInputValue(searchInput, actualPayloadFreeLibraries[0].revisions[0].content_digest)
    })
    expect(container.textContent).toContain('Grand Sunlight Loft')
    expect(container.textContent).not.toContain('Brutalist Concrete Space')

    // 4. Search by translated value: 'Brutalist Concrete Space'
    await act(async () => {
      setInputValue(searchInput, 'Brutalist Concrete Space')
    })
    expect(container.textContent).toContain('Brutalist Concrete Space')
    expect(container.textContent).not.toContain('Grand Sunlight Loft')

    // 5. Search with no matches: 'nonexistent_needle_xyz'
    await act(async () => {
      setInputValue(searchInput, 'nonexistent_needle_xyz')
    })
    expect(container.textContent).not.toContain('Studio Gallery Rooms')
    expect(container.textContent).not.toContain('Summer Couture Scenes')

    // 6. Verify that NO revision detail endpoints were fetched during any of these searches
    const revisionCalls = getSpy.mock.calls.filter(([url]) => url.startsWith('/api/resources/revisions/'))
    expect(revisionCalls.length).toBe(0)
  })
})
