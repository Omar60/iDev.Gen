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

  const typeInputValue = (input, value) => {
    expect(input.disabled).toBe(false)
    input.focus()
    expect(document.activeElement).toBe(input)
    setInputValue(input, value)
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

  it('3.2 loads safe rows and uses only direct bulk preview/apply with the same map and token', async () => {
    const library = actualPayloadFreeLibraries[0]
    const sourceRows = {
      library_key: library.library_key,
      kind: 'rooms',
      diagnostics: [],
      rows: [{
        revision: {
          source_id: library.revisions[0].source_id,
          content_digest: library.revisions[0].content_digest,
        },
        field: 'label',
        source_field: 'name',
        source_value: 'SALLE_A',
        source_shape: 'scalar',
        list_index: null,
        current_translation: 'Existing room',
        translation: 'Existing room',
        required: true,
        role: 'descriptive_input',
        identity_required: false,
        emit_by_default: true,
      }],
    }
    const getSpy = vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) return sourceRows
      return []
    })
    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: library.library_key,
          total_revisions: 1,
          matched_revisions: 1,
          unmatched_map_entries: 0,
          would_update: 1,
          unchanged: 0,
          would_be_ready: 1,
          would_remain_pending: 0,
          attestation_token: 'attested-token',
          expires_at: 123,
        }
      }
      if (url.endsWith('/translations/apply')) return { updated: 1, ready: 1, pending: 0 }
      return {}
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    expect(input.value).toBe('Existing room')
    const preview = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Preview manual translations')
    await act(async () => { preview.click() })

    const directMap = {
      SALLE_A: { source: 'SALLE_A', translation: 'Existing room', fields: ['label'] },
    }
    expect(postSpy).toHaveBeenCalledWith(
      `/api/resources/libraries/${encodeURIComponent(library.library_key)}/translations/preview`,
      { translation_map: directMap }
    )
    const apply = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Confirm & Apply manual translations')
    expect(apply.disabled).toBe(false)
    await act(async () => { apply.click() })

    expect(postSpy).toHaveBeenCalledWith(
      `/api/resources/libraries/${encodeURIComponent(library.library_key)}/translations/apply`,
      { translation_map: directMap, attestation_token: 'attested-token' }
    )
    expect(postSpy.mock.calls.some(([url]) => /\/api\/resources\/revisions\/.*\/translation$/.test(url))).toBe(false)
    expect(getSpy).toHaveBeenCalledWith(
      `/api/resources/libraries/${encodeURIComponent(library.library_key)}/translations/rows`
    )
  })

  it('3.2 invalidates preview on edit and ignores a late preview response', async () => {
    const library = actualPayloadFreeLibraries[0]
    let resolvePreview
    const pendingPreview = new Promise((resolve) => { resolvePreview = resolve })
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: library.library_key,
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label', source_field: 'label', source_value: 'SALLE_A',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: 'Room A', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: true,
          }],
        }
      }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation((url) => (
      url.endsWith('/translations/preview') ? pendingPreview : Promise.resolve({})
    ))

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Load editable rows')
    await act(async () => { load.click() })
    const preview = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Preview manual translations')
    await act(async () => { preview.click(); await Promise.resolve() })

    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    await act(async () => { setInputValue(input, 'Room B') })
    resolvePreview({
      library_key: library.library_key,
      total_revisions: 1,
      matched_revisions: 1,
      would_update: 1,
      would_be_ready: 1,
      would_remain_pending: 0,
      attestation_token: 'stale-token',
    })
    await act(async () => { await pendingPreview })

    const apply = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Confirm & Apply manual translations')
    expect(apply.disabled).toBe(true)
    expect(container.textContent).not.toContain('Manual preview ready')
  })

  it.each([409, 422])('3.2 fails closed and invalidates preview authorization on apply error %i', async (statusCode) => {
    const library = actualPayloadFreeLibraries[0]
    const sourceRows = {
      library_key: library.library_key,
      kind: 'rooms',
      diagnostics: [],
      rows: [{
        revision: {
          source_id: library.revisions[0].source_id,
          content_digest: library.revisions[0].content_digest,
        },
        field: 'label',
        source_field: 'name',
        source_value: 'SALLE_A',
        source_shape: 'scalar',
        list_index: null,
        current_translation: 'Existing room',
        translation: 'Existing room',
        required: true,
        role: 'descriptive_input',
        identity_required: false,
        emit_by_default: true,
      }],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) return sourceRows
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: library.library_key,
          total_revisions: 1,
          matched_revisions: 1,
          unmatched_map_entries: 0,
          would_update: 1,
          unchanged: 0,
          would_be_ready: 1,
          would_remain_pending: 0,
          attestation_token: 'auth-token-xyz',
          expires_at: 123,
        }
      }
      if (url.endsWith('/translations/apply')) {
        const error = new Error(`Apply failed with HTTP ${statusCode}`)
        error.status = statusCode
        throw error
      }
      return {}
    })

    await renderComponent()

    // 1. Load rows
    const loadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { loadBtn.click() })

    // 2. Preview valid
    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview manual translations')
    await act(async () => { previewBtn.click() })

    // 3. manualPreview / token available
    expect(container.textContent).toContain('Manual preview ready')

    // 4. Apply enabled
    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    expect(applyBtn.disabled).toBe(false)

    // 5. Apply responds with error (409 / 422)
    await act(async () => { applyBtn.click() })

    // 6. Post-state: fail-closed
    expect(container.textContent).not.toContain('Manual preview ready')
    expect(applyBtn.disabled).toBe(true)
    expect(container.textContent).toContain(`Apply failed with HTTP ${statusCode}`)
  })

  it('3.3 selection bound: suggests action disabled when 0 selected or > 20 selected', async () => {
    const library = actualPayloadFreeLibraries[0]
    const rows = Array.from({ length: 21 }, (_, i) => ({
      revision: { source_id: `room-${i}`, content_digest: `${i}`.padStart(64, '0') },
      field: 'label',
      source_field: 'name',
      source_value: `SALLE_${i}`,
      source_shape: 'scalar',
      list_index: null,
      current_translation: null,
      translation: '',
      required: true,
      role: 'descriptive_input',
      identity_required: false,
      emit_by_default: false,
    }))

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        return { library_key: library.library_key, kind: 'rooms', diagnostics: [], rows }
      }
      return []
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.startsWith('Suggest translations'))
    // 0 selected: disabled
    expect(suggestBtn.disabled).toBe(true)

    // Select 1 checkbox: enabled
    const checkboxes = container.querySelectorAll('input[aria-label^="Suggest translation for"]')
    expect(checkboxes.length).toBe(21)
    await act(async () => { checkboxes[0].click() })
    expect(suggestBtn.disabled).toBe(false)
    expect(container.textContent).toContain('1 selected')

    // Select remaining 20 checkboxes -> 21 selected: disabled
    await act(async () => {
      for (let i = 1; i < 21; i++) {
        checkboxes[i].click()
      }
    })
    expect(suggestBtn.disabled).toBe(true)
    expect(container.textContent).toContain('21 selected (max 20)')
  })

  it('3.3 executes Suggest flow: sends exact identity-only entries, merges proposals, invalidates preview, allows discard', async () => {
    const library = actualPayloadFreeLibraries[0]
    const sourceRows = {
      library_key: library.library_key,
      kind: 'rooms',
      diagnostics: [],
      rows: [{
        revision: {
          source_id: 'room-1',
          content_digest: 'a'.repeat(64),
        },
        field: 'label',
        source_field: 'name',
        source_value: 'SALLE_A',
        source_shape: 'scalar',
        list_index: null,
        current_translation: null,
        translation: '',
        required: true,
        role: 'descriptive_input',
        identity_required: false,
        emit_by_default: false,
      }],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) return sourceRows
      return []
    })

    const postSpy = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/proposals')) {
        return {
          library_key: library.library_key,
          proposals: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label',
            source_shape: 'scalar',
            list_index: null,
            source_value: 'SALLE_A',
            translation: 'Room Alpha',
          }],
        }
      }
      return {}
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    expect(input.value).toBe('')

    // Select row for suggest
    const checkbox = container.querySelector('input[aria-label="Suggest translation for SALLE_A"]')
    await act(async () => { checkbox.click() })

    // Click Suggest translations
    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Suggest translations')
    await act(async () => { suggestBtn.click() })

    // 1. Verify exact request payload contains ONLY entries with identity keys (no source_value or client translation)
    expect(postSpy).toHaveBeenCalledWith(
      `/api/resources/libraries/${encodeURIComponent(library.library_key)}/translations/proposals`,
      {
        entries: [{
          source_id: 'room-1',
          content_digest: 'a'.repeat(64),
          field: 'label',
          source_shape: 'scalar',
          list_index: null,
        }],
      }
    )

    // 2. Value was merged into editable row and emitted
    expect(input.value).toBe('Room Alpha')
    const useCheckbox = container.querySelector('input[aria-label="Use translation for SALLE_A"]')
    expect(useCheckbox.checked).toBe(true)

    // 3. Confirm & Apply remains disabled (no auto preview or apply)
    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    expect(applyBtn.disabled).toBe(true)
    expect(postSpy.mock.calls.some(([url]) => url.endsWith('/translations/preview'))).toBe(false)
    expect(postSpy.mock.calls.some(([url]) => url.endsWith('/translations/apply'))).toBe(false)
    expect(postSpy.mock.calls.some(([url]) => /\/revisions\/.*\/translation$/.test(url))).toBe(false)

    // 4. Discard suggestions restores pre-proposal row snapshot
    const discardBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Discard suggestions')
    expect(discardBtn).not.toBeNull()
    await act(async () => { discardBtn.click() })
    expect(input.value).toBe('')
  })

  it('3.3 stale response fencing: ignores late Suggest response after row edit or reload', async () => {
    const library = actualPayloadFreeLibraries[0]
    let resolveSuggest
    const pendingSuggest = new Promise((resolve) => { resolveSuggest = resolve })

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: library.library_key,
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'SALLE_A',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: '', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: false,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation((url) => (
      url.endsWith('/translations/proposals') ? pendingSuggest : Promise.resolve({})
    ))

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const checkbox = container.querySelector('input[aria-label="Suggest translation for SALLE_A"]')
    await act(async () => { checkbox.click() })

    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Suggest translations')
    await act(async () => { suggestBtn.click() })

    // User manually types during in-flight Suggest
    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    await act(async () => { setInputValue(input, 'User Custom Room') })

    // Immediately confirmed: proposal generation invalidated, proposalBusy is false, Suggest button re-enabled
    const recheckedSuggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    expect(recheckedSuggestBtn).toBeTruthy()
    expect(recheckedSuggestBtn.textContent).not.toContain('Suggesting')
    expect(recheckedSuggestBtn.disabled).toBe(false)

    // Now late suggest response arrives
    resolveSuggest({
      library_key: library.library_key,
      proposals: [{
        revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
        field: 'label', source_shape: 'scalar', list_index: null,
        source_value: 'SALLE_A', translation: 'Stale Room',
      }],
    })
    await act(async () => { await pendingSuggest })

    // User edit must be preserved; stale proposal ignored, busy remains false
    expect(input.value).toBe('User Custom Room')
    const finalSuggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    expect(finalSuggestBtn.textContent).not.toContain('Suggesting')
    expect(finalSuggestBtn.disabled).toBe(false)
  })

  it('3.3 reload during suggest resets proposalBusy and late response is ignored', async () => {
    const library = actualPayloadFreeLibraries[0]
    let resolveSuggest
    const pendingSuggest = new Promise((res) => { resolveSuggest = res })

    let rowLoadCount = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        rowLoadCount += 1
        return {
          library_key: library.library_key,
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label', source_field: 'name', source_value: `SALLE_RELOAD_${rowLoadCount}`,
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: '', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: false,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation((url) => (
      url.endsWith('/translations/proposals') ? pendingSuggest : Promise.resolve({})
    ))

    await renderComponent()
    const loadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { loadBtn.click() })

    const checkbox = container.querySelector('input[aria-label="Suggest translation for SALLE_RELOAD_1"]')
    await act(async () => { checkbox.click() })

    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    await act(async () => { suggestBtn.click() })

    // Suggest A is in-flight; reload rows
    await act(async () => { loadBtn.click() })

    // Busy cleared immediately on reload, UI is unblocked
    const suggestBtnAfterReload = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    expect(suggestBtnAfterReload.textContent).not.toContain('Suggesting')

    // Suggest A resolves late
    resolveSuggest({
      library_key: library.library_key,
      proposals: [{
        revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
        field: 'label', source_shape: 'scalar', list_index: null,
        source_value: 'SALLE_RELOAD_1', translation: 'Stale Overwrite Room',
      }],
    })
    await act(async () => { await pendingSuggest })

    // Stale response did NOT overwrite new rows
    const input = container.querySelector('input[aria-label="Translation for SALLE_RELOAD_2"]')
    expect(input).toBeTruthy()
    expect(input.value).toBe('')
    expect(container.textContent).not.toContain('Stale Overwrite Room')
  })

  it('3.3 global reload from another library clears proposalBusy and fences the stale response', async () => {
    const [libraryA, libraryB] = actualPayloadFreeLibraries
    let resolveSuggestA
    const pendingSuggestA = new Promise((resolve) => { resolveSuggestA = resolve })

    const rowsByLibrary = {
      [libraryA.library_key]: {
        library_key: libraryA.library_key,
        kind: libraryA.kind,
        diagnostics: [],
        rows: [{
          revision: { source_id: 'room-a', content_digest: 'a'.repeat(64) },
          field: 'label', source_field: 'name', source_value: 'SALLE_A',
          source_shape: 'scalar', list_index: null, current_translation: null,
          translation: 'Current A', required: true, role: 'descriptive_input',
          identity_required: false, emit_by_default: true,
        }],
      },
      [libraryB.library_key]: {
        library_key: libraryB.library_key,
        kind: libraryB.kind,
        diagnostics: [],
        rows: [{
          revision: { source_id: 'scene-b', content_digest: 'b'.repeat(64) },
          field: 'prompt', source_field: 'prompt', source_value: 'SCENE_B',
          source_shape: 'scalar', list_index: null, current_translation: null,
          translation: 'Current B', required: true, role: 'descriptive_input',
          identity_required: false, emit_by_default: true,
        }],
      },
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [libraryA, libraryB]
      if (url === '/api/models') return []
      const match = url.match(/\/api\/resources\/libraries\/([^/]+)\/translations\/rows$/)
      if (match) return rowsByLibrary[decodeURIComponent(match[1])]
      return []
    })

    vi.spyOn(api, 'post').mockImplementation((url) => {
      if (url.includes(encodeURIComponent(libraryA.library_key)) && url.endsWith('/translations/proposals')) {
        return pendingSuggestA
      }
      if (url.endsWith('/translations/preview')) {
        return Promise.resolve({
          library_key: libraryB.library_key,
          total_revisions: 1,
          matched_revisions: 1,
          unmatched_map_entries: 0,
          would_update: 1,
          unchanged: 0,
          would_be_ready: 1,
          would_remain_pending: 0,
          attestation_token: 'library-b-token',
          expires_at: 123,
        })
      }
      if (url.endsWith('/translations/apply')) {
        return Promise.resolve({ updated: 1, ready: 1, pending: 0 })
      }
      return Promise.resolve({})
    })

    await renderComponent()
    const panelA = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === libraryA.display_name).closest('.panel')
    const panelB = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === libraryB.display_name).closest('.panel')

    const loadA = Array.from(panelA.querySelectorAll('button')).find((button) => button.textContent === 'Load editable rows')
    const loadB = Array.from(panelB.querySelectorAll('button')).find((button) => button.textContent === 'Load editable rows')
    await act(async () => { loadA.click(); loadB.click() })

    const selectA = panelA.querySelector('input[aria-label="Suggest translation for SALLE_A"]')
    await act(async () => { selectA.click() })
    const suggestA = Array.from(panelA.querySelectorAll('button')).find((button) => button.textContent === 'Suggest translations')
    await act(async () => { suggestA.click() })
    expect(panelA.textContent).toContain('Suggesting…')

    const previewB = Array.from(panelB.querySelectorAll('button')).find((button) => button.textContent === 'Preview manual translations')
    await act(async () => { previewB.click() })
    const applyB = Array.from(panelB.querySelectorAll('button')).find((button) => button.textContent === 'Confirm & Apply manual translations')
    await act(async () => { applyB.click() })

    const suggestAfterReload = Array.from(panelA.querySelectorAll('button')).find((button) => button.textContent.includes('Suggest'))
    expect(suggestAfterReload.textContent).toBe('Suggest translations')
    expect(suggestAfterReload.disabled).toBe(false)

    resolveSuggestA({
      library_key: libraryA.library_key,
      proposals: [{
        revision: { source_id: 'room-a', content_digest: 'a'.repeat(64) },
        field: 'label', source_shape: 'scalar', list_index: null,
        source_value: 'SALLE_A', translation: 'Stale A',
      }],
    })
    await act(async () => { await pendingSuggestA })

    expect(panelA.querySelector('input[aria-label="Translation for SALLE_A"]').value).toBe('Current A')
    expect(panelA.textContent).not.toContain('Stale A')
    expect(suggestAfterReload.textContent).toBe('Suggest translations')
    expect(suggestAfterReload.disabled).toBe(false)
  })

  it('3.3 second suggest flow when first suggest invalidated by edit', async () => {
    const library = actualPayloadFreeLibraries[0]
    let resolveSuggestA
    const pendingSuggestA = new Promise((res) => { resolveSuggestA = res })
    let resolveSuggestB
    const pendingSuggestB = new Promise((res) => { resolveSuggestB = res })

    let callCount = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: library.library_key,
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'SALLE_A',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: '', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: false,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation((url) => {
      if (url.endsWith('/translations/proposals')) {
        callCount += 1
        if (callCount === 1) return pendingSuggestA
        return pendingSuggestB
      }
      return Promise.resolve({})
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const checkbox = container.querySelector('input[aria-label="Suggest translation for SALLE_A"]')
    await act(async () => { checkbox.click() })

    // Start Suggest A
    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    await act(async () => { suggestBtn.click() })

    // User edits while A is pending -> invalidates A, clears busy
    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    await act(async () => { setInputValue(input, 'Interim Edit') })

    // Verify checkbox is still selected, then trigger Suggest B
    expect(checkbox.checked).toBe(true)
    const suggestBtnB = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Suggest translations'))
    expect(suggestBtnB.disabled).toBe(false)
    await act(async () => { suggestBtnB.click() })

    // Suggest B resolves first
    resolveSuggestB({
      library_key: library.library_key,
      proposals: [{
        revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
        field: 'label', source_shape: 'scalar', list_index: null,
        source_value: 'SALLE_A', translation: 'Translation B',
      }],
    })
    await act(async () => { await pendingSuggestB })
    expect(input.value).toBe('Translation B')

    // Now Suggest A arrives late
    resolveSuggestA({
      library_key: library.library_key,
      proposals: [{
        revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
        field: 'label', source_shape: 'scalar', list_index: null,
        source_value: 'SALLE_A', translation: 'Translation A (Stale)',
      }],
    })
    await act(async () => { await pendingSuggestA })

    // A did NOT overwrite B!
    expect(input.value).toBe('Translation B')
  })

  it('3.3 provider failure preserves existing manual rows without modification', async () => {
    const library = actualPayloadFreeLibraries[0]
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: library.library_key,
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: 'a'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'SALLE_A',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: 'Pre-existing Translation', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: true,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/proposals')) {
        const error = new Error('The prompt assistant returned an invalid proposal response.')
        error.status = 502
        throw error
      }
      return {}
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const checkbox = container.querySelector('input[aria-label="Suggest translation for SALLE_A"]')
    await act(async () => { checkbox.click() })

    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Suggest translations')
    await act(async () => { suggestBtn.click() })

    // Error is displayed, but existing manual translation remains completely unchanged
    expect(container.textContent).toContain('invalid proposal response')
    const input = container.querySelector('input[aria-label="Translation for SALLE_A"]')
    expect(input.value).toBe('Pre-existing Translation')
  })

  // =========================================================================
  // Task 3.4 Specification & Contract Tests
  // =========================================================================

  it('3.4 presents Needs translation with direct Translate action that loads rows', async () => {
    const library = {
      id: 1,
      library_key: 'lib_pending',
      display_name: 'Pending Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'lib_pending',
        source_id: 'room-pending',
        content_digest: 'd'.repeat(64),
        readiness: {
          status: 'pending',
          pending_fields: { scene_theme: 'Missing translation' },
        },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: 'lib_pending',
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-pending', content_digest: 'd'.repeat(64) },
            field: 'scene_theme',
            source_field: 'theme',
            source_value: 'Grand theme',
            source_shape: 'scalar',
            list_index: null,
            current_translation: null,
            translation: '',
            required: true,
            role: 'descriptive_input',
            identity_required: false,
            emit_by_default: false,
          }],
        }
      }
      return []
    })

    await renderComponent()

    expect(container.textContent).toContain('Needs translation')
    expect(container.textContent).toContain('Missing translation')

    const buttons = Array.from(container.querySelectorAll('button'))
    const translateBtn = buttons.find((b) => b.textContent === 'Translate')
    const createSessionBtn = buttons.find((b) => b.textContent === 'Create session')
    expect(translateBtn).toBeTruthy()
    expect(createSessionBtn).toBeFalsy()

    await act(async () => { translateBtn.click() })
    expect(api.get).toHaveBeenCalledWith('/api/resources/libraries/lib_pending/translations/rows')
  })

  it('3.4 presents Ready with enabled Create session when model selected, disabled without model', async () => {
    const library = {
      id: 1,
      library_key: 'lib_ready',
      display_name: 'Ready Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'lib_ready',
        source_id: 'room-ready',
        content_digest: 'r'.repeat(64),
        readiness: {
          status: 'ready',
          pending_fields: {},
        },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/sessions') return { id: 99, name: 'Model A - room-ready' }
      return {}
    })
    vi.spyOn(api, 'patch').mockResolvedValue({})

    await renderComponent()

    expect(container.textContent).toContain('Ready')
    const createSessionBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Create session'
    )
    expect(createSessionBtn).toBeTruthy()
    expect(createSessionBtn.disabled).toBe(false)

    await act(async () => { createSessionBtn.click() })
    expect(api.post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({
      model_id: 1,
      composition_mode: 'resource-v1',
    }))
  })

  it('3.4 presents Needs source correction with Inspect source action and no translation cure', async () => {
    const library = {
      id: 1,
      library_key: 'lib_malformed',
      display_name: 'Malformed Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'lib_malformed',
        source_id: 'room-malformed',
        content_digest: 'm'.repeat(64),
        readiness: {
          status: 'pending',
          diagnostics: [{
            code: 'invalid_source_shape',
            field: 'label',
            message: "Source field 'name' must be a non-empty scalar string",
          }],
        },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      if (url.startsWith('/api/resources/revisions/')) {
        return {
          ...library.revisions[0],
          payload: { name: ['Invalid list'] },
          readiness: library.revisions[0].readiness,
        }
      }
      return []
    })

    await renderComponent()

    expect(container.textContent).toContain('Needs source correction')
    expect(container.textContent).toContain("Source field 'name' must be a non-empty scalar string")

    const buttons = Array.from(container.querySelectorAll('button'))
    expect(buttons.some((b) => b.textContent === 'Translate')).toBe(false)
    expect(buttons.some((b) => b.textContent === 'Create session')).toBe(false)

    const inspectBtn = buttons.find((b) => b.textContent === 'Inspect source')
    expect(inspectBtn).toBeTruthy()

    await act(async () => { inspectBtn.click() })
    expect(container.textContent).toContain('Source Diagnostics:')
    expect(container.textContent).toContain('invalid_source_shape')
  })

  it('3.4 manual Apply awaits fresh library reload and displays Ready without browser refresh', async () => {
    let isReady = false
    const pendingLib = {
      id: 1,
      library_key: 'manual_apply_lib',
      display_name: 'Manual Apply Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'manual_apply_lib',
        source_id: 'room-1',
        content_digest: '1'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    const readyLib = {
      ...pendingLib,
      revisions: [{
        ...pendingLib.revisions[0],
        readiness: { status: 'ready', pending_fields: {} },
      }],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [isReady ? readyLib : pendingLib]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: 'manual_apply_lib',
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: '1'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'Salon',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: 'Living Room', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: true,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: 'manual_apply_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0, would_be_ready: 1,
          would_remain_pending: 0, attestation_token: 'token_123', expires_at: 9999999999,
        }
      }
      if (url.endsWith('/translations/apply')) {
        isReady = true
        return { updated: 1, ready: 1, pending: 0 }
      }
      return {}
    })

    await renderComponent()
    expect(container.textContent).toContain('Needs translation')

    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview manual translations')
    await act(async () => { previewBtn.click() })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    await act(async () => { applyBtn.click() })

    expect(container.textContent).toContain('Ready')
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent === 'Create session')).toBe(true)
  })

  it('3.4 map Apply clears preview, awaits fresh library reload, and updates visible state', async () => {
    let isReady = false
    const pendingLib = {
      id: 1,
      library_key: 'map_apply_lib',
      display_name: 'Map Apply Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'map_apply_lib',
        source_id: 'room-1',
        content_digest: '2'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    const readyLib = {
      ...pendingLib,
      revisions: [{
        ...pendingLib.revisions[0],
        readiness: { status: 'ready', pending_fields: {} },
      }],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [isReady ? readyLib : pendingLib]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: 'map_apply_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0, would_be_ready: 1,
          would_remain_pending: 0, attestation_token: 'map_token_abc', expires_at: 9999999999,
        }
      }
      if (url.endsWith('/translations/apply')) {
        isReady = true
        return { updated: 1, ready: 1, pending: 0 }
      }
      return {}
    })

    await renderComponent()
    expect(container.textContent).toContain('Needs translation')

    const input = container.querySelector('input[placeholder*="translations.json"]')
    await act(async () => { setInputValue(input, '/path/to/map.json') })

    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations')
    await act(async () => { previewBtn.click() })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Confirm & Apply Translations'))
    expect(applyBtn).toBeTruthy()

    await act(async () => { applyBtn.click() })

    expect(container.textContent).toContain('Ready')
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)
  })

  it('3.4 failed Apply does not fake readiness and clears preview authorization', async () => {
    const pendingLib = {
      id: 1,
      library_key: 'fail_lib',
      display_name: 'Fail Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'fail_lib',
        source_id: 'room-1',
        content_digest: '3'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [pendingLib]
      if (url === '/api/models') return [{ id: 1, name: 'Model A' }]
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: 'fail_lib', kind: 'rooms', diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: '3'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'Salon',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: 'Living Room', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: true,
          }],
        }
      }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: 'fail_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0, would_be_ready: 1,
          would_remain_pending: 0, attestation_token: 'token_fail', expires_at: 9999999999,
        }
      }
      if (url.endsWith('/translations/apply')) {
        throw new Error('Conflict: translation state has drifted')
      }
      return {}
    })

    await renderComponent()
    const load = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { load.click() })

    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview manual translations')
    await act(async () => { previewBtn.click() })

    const applyBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    await act(async () => { applyBtn.click() })

    expect(container.textContent).toContain('Conflict')
    expect(container.textContent).toContain('Needs translation')
    expect(container.textContent).not.toContain('Manual preview ready')
    const applyBtnAfter = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    expect(applyBtnAfter.disabled).toBe(true)
  })

  it('3.4 older library refresh cannot overwrite newer refresh (epoch fencing)', async () => {
    let resolveRefreshA
    const refreshAPromise = new Promise((resolve) => { resolveRefreshA = resolve })

    const libV1 = [{
      id: 1, library_key: 'fence_lib', display_name: 'V1 Old Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'fence_lib', source_id: 'room-1', content_digest: '4'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }]

    const libV2 = [{
      id: 1, library_key: 'fence_lib', display_name: 'V2 Fresh Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'fence_lib', source_id: 'room-1', content_digest: '4'.repeat(64),
        readiness: { status: 'ready', pending_fields: {} },
      }],
      auxiliary: [],
    }]

    let callCount = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        callCount++
        if (callCount === 1) return libV1
        if (callCount === 2) return refreshAPromise
        if (callCount === 3) return libV2
      }
      if (url === '/api/models') return []
      return []
    })

    await renderComponent()
    expect(container.textContent).toContain('V1 Old Lib')

    const refreshBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Refresh')

    await act(async () => { refreshBtn.click() })
    await act(async () => { refreshBtn.click() })

    expect(container.textContent).toContain('V2 Fresh Lib')

    await act(async () => { resolveRefreshA(libV1) })

    expect(container.textContent).toContain('V2 Fresh Lib')
    expect(container.textContent).not.toContain('V1 Old Lib')
  })

  it('3.4 mapPath change immediately invalidates old preview and token', async () => {
    const library = {
      id: 1, library_key: 'inval_lib', display_name: 'Inval Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'inval_lib', source_id: 'room-1', content_digest: '5'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return {
          library_key: 'inval_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0, would_be_ready: 1,
          would_remain_pending: 0, attestation_token: 'token_path_a', expires_at: 9999999999,
        }
      }
      return {}
    })

    await renderComponent()
    const input = container.querySelector('input[placeholder*="translations.json"]')
    await act(async () => { setInputValue(input, '/path/a.json') })

    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations')
    await act(async () => { previewBtn.click() })

    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(true)

    await act(async () => { setInputValue(input, '/path/b.json') })

    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)
  })

  it('3.4 late map Preview cannot re-enable Apply after path change', async () => {
    let resolvePreviewA
    const previewAPromise = new Promise((resolve) => { resolvePreviewA = resolve })

    const library = {
      id: 1, library_key: 'late_map_lib', display_name: 'Late Map Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'late_map_lib', source_id: 'room-1', content_digest: '6'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return []
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/preview')) {
        return previewAPromise
      }
      return {}
    })

    await renderComponent()
    const input = container.querySelector('input[placeholder*="translations.json"]')
    await act(async () => { setInputValue(input, '/path/a.json') })

    const previewBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations')
    await act(async () => { previewBtn.click() })

    await act(async () => { setInputValue(input, '/path/b.json') })

    await act(async () => {
      resolvePreviewA({
        library_key: 'late_map_lib', total_revisions: 1, matched_revisions: 1,
        unmatched_map_entries: 0, would_update: 1, unchanged: 0, would_be_ready: 1,
        would_remain_pending: 0, attestation_token: 'token_a', expires_at: 9999999999,
      })
    })

    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)
  })

  it('3.4 import commit awaits readiness refresh and makes no auto-translate or session POST', async () => {
    const postCalls = []
    const freshLibrary = [{
      id: 1, library_key: 'imported_lib', display_name: 'Imported Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'imported_lib', source_id: 'room-new', content_digest: '7'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Needs translation' } },
      }],
      auxiliary: [],
    }]

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return freshLibrary
      if (url === '/api/models') return []
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      postCalls.push({ url, body })
      return {}
    })

    await renderComponent()
    await switchToImportTab()

    expect(container.textContent).toContain('Import Preview')
    expect(postCalls.some((c) => c.url.includes('/translations/apply'))).toBe(false)
    expect(postCalls.some((c) => c.url.includes('/translations/proposals'))).toBe(false)
    expect(postCalls.some((c) => c.url === '/api/sessions')).toBe(false)
  })

  // ==========================================
  // Task 3.4 Blocker Regressions & Verifications
  // ==========================================

  it('3.4 Blocker 1: Re-import switches to Import tab without ReferenceError', async () => {
    const errorLib = {
      id: 1,
      library_key: 'broken_source_lib',
      display_name: 'Broken Source Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'broken_source_lib',
        source_id: 'room-corrupt',
        content_digest: 'a'.repeat(64),
        readiness: {
          status: 'pending',
          diagnostics: [{
            code: 'invalid_source_shape',
            field: 'name',
            message: 'Source field has an invalid shape.',
          }],
        },
      }],
      auxiliary: [],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [errorLib]
      if (url === '/api/models') return []
      return []
    })

    await renderComponent()
    expect(container.textContent).toContain('Needs source correction')

    const reimportBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Re-import')
    expect(reimportBtn).toBeTruthy()

    // Must not throw ReferenceError: setActiveTab is not defined
    await act(async () => {
      reimportBtn.click()
    })

    // Active tab is now Import
    expect(container.textContent).toContain('Import Preview')
  })

  const makeOpenViewWithPreview = (selectionId, libKey, sourceId, digest) => ({
    selection_id: selectionId,
    selection_revision: 2,
    state: 'open',
    expires_at: '2026-09-17T20:00:00Z',
    files: [{
      file_id: 'f1',
      file_name: 'f1.json',
      byte_count: 10,
      declared_library: libKey,
      effective_library_key: libKey,
      matched_auxiliary_kinds: [],
      effective_auxiliary_kind: null,
      status: 'staged',
    }],
    preview: {
      preview_token: 'valid-ptok-123',
      manifest_digest: digest,
      committable: true,
      report: {
        version: 1,
        phase: 'preview',
        summary: {
          accepted: 1,
          auxiliary: 0,
          duplicates: 0,
          files: 1,
          inputs: 1,
          missing: 0,
          new: 1,
          unchanged: 0,
          unresolved: 0,
          updated: 0,
        },
        files: [{
          accepted: [{
            classification: 'new',
            kind: 'rooms',
            library_key: libKey,
            new_content_digest: digest,
            previous_content_digest: null,
            source_id: sourceId,
          }],
          auxiliary: [],
          duplicates: [],
          library_key: libKey,
          total_inputs: 1,
          unresolved: [],
        }],
        missing_source_entries: [],
      },
    },
    commit_result: null,
    cleanup_warning: null,
  })

  const makeCommittedView = (openView, libKey, sourceId, digest) => ({
    ...openView,
    selection_revision: openView.selection_revision + 1,
    state: 'committed',
    commit_result: {
      version: 1,
      phase: 'commit',
      summary: {
        accepted: 1,
        auxiliary: 0,
        duplicates: 0,
        files: 1,
        inputs: 1,
        missing: 0,
        new: 1,
        new_auxiliary_revisions: 0,
        new_scene_revisions: 1,
        recorded: 1,
        unchanged: 0,
        unchanged_auxiliary_revisions: 0,
        unchanged_scene_revisions: 0,
        unresolved: 0,
        updated: 0,
        updated_scene_revisions: 0,
      },
      files: [{
        accepted: [{
          classification: 'new',
          kind: 'rooms',
          library_key: libKey,
          new_content_digest: digest,
          previous_content_digest: null,
          source_id: sourceId,
        }],
        auxiliary: [],
        duplicates: [],
        library_key: libKey,
        total_inputs: 1,
        unresolved: [],
      }],
      missing_source_entries: [],
    },
  })

  it('3.4 Blocker 1: View readiness in Inventory switches to Inventory tab without ReferenceError and reloads libraries', async () => {
    let getLibrariesCount = 0
    const digest = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
    const sampleLib = {
      id: 1,
      library_key: 'sample_lib',
      display_name: 'Sample Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'sample_lib',
        source_id: 'room-1',
        content_digest: digest,
        readiness: { status: 'ready', pending_fields: {} },
      }],
      auxiliary: [],
    }

    const openView = makeOpenViewWithPreview('sel-report-test', 'sample_lib', 'room-1', digest)
    const committedView = makeCommittedView(openView, 'sample_lib', 'room-1', digest)

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        getLibrariesCount++
        return [sampleLib]
      }
      return []
    })

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

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn).toBeTruthy()
    await act(async () => {
      commitBtn.click()
    })

    expect(container.textContent).toContain('Commit Report')
    const viewReadinessBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('View readiness in Inventory'))
    expect(viewReadinessBtn).toBeTruthy()

    const countBefore = getLibrariesCount
    // Must not throw ReferenceError: setActiveTab is not defined, must await reloadLibraries and switch to Inventory tab
    await act(async () => {
      viewReadinessBtn.click()
    })

    expect(getLibrariesCount).toBeGreaterThan(countBefore)
    expect(container.textContent).toContain('Sample Lib')
  })

  it('3.4 Blocker 3: Commit Import awaits GET /api/resources/libraries before showing update notice', async () => {
    let resolveGetLibraries
    const getLibrariesPromise = new Promise((resolve) => { resolveGetLibraries = resolve })
    const digest = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'

    const sampleLib = {
      id: 1,
      library_key: 'sample_lib',
      display_name: 'Sample Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'sample_lib',
        source_id: 'room-1',
        content_digest: digest,
        readiness: { status: 'ready', pending_fields: {} },
      }],
      auxiliary: [],
    }

    const openView = makeOpenViewWithPreview('sel-await-test', 'sample_lib', 'room-1', digest)
    const committedView = makeCommittedView(openView, 'sample_lib', 'room-1', digest)

    let initialLoadDone = false
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        if (!initialLoadDone) {
          initialLoadDone = true
          return [sampleLib]
        }
        return getLibrariesPromise
      }
      return []
    })

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

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn).toBeTruthy()
    await act(async () => {
      commitBtn.click()
    })

    // Commit POST has succeeded, but GET /api/resources/libraries is still pending:
    // Update notice MUST NOT be shown yet
    expect(container.textContent).not.toContain('Local resource inventory updated')

    // Now resolve GET /api/resources/libraries
    await act(async () => {
      resolveGetLibraries([sampleLib])
    })

    // Update notice MUST now be shown
    expect(container.textContent).toContain('Local resource inventory updated')
  })

  it('3.4 Blocker 3: Commit Import with GET rejection displays refresh error and does not re-commit or post session', async () => {
    const postCalls = []
    const digest = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
    const sampleLib = {
      id: 1,
      library_key: 'sample_lib',
      display_name: 'Sample Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'sample_lib',
        source_id: 'room-1',
        content_digest: digest,
        readiness: { status: 'ready', pending_fields: {} },
      }],
      auxiliary: [],
    }

    const openView = makeOpenViewWithPreview('sel-reject-test', 'sample_lib', 'room-1', digest)
    const committedView = makeCommittedView(openView, 'sample_lib', 'room-1', digest)

    let initialLoadDone = false
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        if (!initialLoadDone) {
          initialLoadDone = true
          return [sampleLib]
        }
        throw new Error('Database locked during reload')
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      postCalls.push({ url, body })
      if (url === '/api/resources/import-selections') return openView
      if (url.endsWith('/commit')) return committedView
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)

    await renderComponent()
    await switchToImportTab()

    await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
    await clickUpload()

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn).toBeTruthy()
    await act(async () => {
      commitBtn.click()
    })

    // Refresh error is displayed
    expect(container.textContent).toContain('Import committed, but failed to refresh inventory: Database locked during reload')
    // Notice is withheld
    expect(container.textContent).not.toContain('Local resource inventory updated')
    // No automatic session post or auto-translate
    expect(postCalls.some((c) => c.url === '/api/sessions')).toBe(false)
    expect(postCalls.some((c) => c.url.includes('/translations/apply'))).toBe(false)
  })

  it('3.4 Blocker 2: Newly committed revision displays Imported badge alongside readiness; historical revision does not', async () => {
    const newDigest = '1111111111111111111111111111111111111111111111111111111111111111'
    const histDigest = '2222222222222222222222222222222222222222222222222222222222222222'

    const multiRevLib = {
      id: 1,
      library_key: 'sample_lib',
      display_name: 'Sample Lib',
      kind: 'rooms',
      revisions: [
        {
          revision_id: 1,
          library_key: 'sample_lib',
          source_id: 'room-new',
          content_digest: newDigest,
          readiness: { status: 'pending', pending_fields: { label: 'Need translation' } },
        },
        {
          revision_id: 2,
          library_key: 'sample_lib',
          source_id: 'room-historical',
          content_digest: histDigest,
          readiness: { status: 'ready', pending_fields: {} },
        },
      ],
      auxiliary: [],
    }

    const openView = makeOpenViewWithPreview('sel-imported-test', 'sample_lib', 'room-new', newDigest)
    const committedView = makeCommittedView(openView, 'sample_lib', 'room-new', newDigest)

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [multiRevLib]
      return []
    })

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

    const commitBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import'))
    expect(commitBtn).toBeTruthy()
    await act(async () => {
      commitBtn.click()
    })

    // Now switch to Inventory tab
    const viewReadinessBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('View readiness in Inventory'))
    expect(viewReadinessBtn).toBeTruthy()
    await act(async () => {
      viewReadinessBtn.click()
    })

    // Look at table rows in Inventory
    const rows = Array.from(container.querySelectorAll('table tbody tr'))
    const newRow = rows.find((r) => r.textContent.includes('room-new'))
    const histRow = rows.find((r) => r.textContent.includes('room-historical'))

    expect(newRow).toBeTruthy()
    expect(histRow).toBeTruthy()

    // newRow has Imported chip AND Needs translation badge
    expect(newRow.textContent).toContain('Imported')
    expect(newRow.textContent).toContain('Needs translation')

    // histRow has Ready badge, but NOT Imported chip
    expect(histRow.textContent).toContain('Ready')
    expect(histRow.textContent).not.toContain('Imported')
  })

  it('3.4 Blocker 5: Manual edit after suggestions clears preProposalRows and hides Discard suggestions', async () => {
    const pendingLib = {
      id: 1,
      library_key: 'suggest_lib',
      display_name: 'Suggest Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'suggest_lib',
        source_id: 'room-1',
        content_digest: '8'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [pendingLib]
      if (url.endsWith('/translations/rows')) {
        return {
          library_key: 'suggest_lib',
          kind: 'rooms',
          diagnostics: [],
          rows: [{
            revision: { source_id: 'room-1', content_digest: '8'.repeat(64) },
            field: 'label', source_field: 'name', source_value: 'Bureau',
            source_shape: 'scalar', list_index: null, current_translation: null,
            translation: '', required: true, role: 'descriptive_input',
            identity_required: false, emit_by_default: true,
          }],
        }
      }
      return []
    })

    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/translations/proposals')) {
        return {
          library_key: 'suggest_lib',
          proposals: [{
            revision: { source_id: 'room-1', content_digest: '8'.repeat(64) },
            field: 'label', source_shape: 'scalar', list_index: null,
            translation: 'Suggested Office',
          }],
        }
      }
      return {}
    })

    await renderComponent()
    const loadBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows')
    await act(async () => { loadBtn.click() })

    // Select the suggest checkbox for the row
    const suggestCheckbox = container.querySelector('input[aria-label="Suggest translation for Bureau"]')
    expect(suggestCheckbox).toBeTruthy()
    await act(async () => { suggestCheckbox.click() })

    // Suggest translations
    const suggestBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Suggest translations')
    expect(suggestBtn.disabled).toBe(false)
    await act(async () => { suggestBtn.click() })

    // "Discard suggestions" should appear
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent === 'Discard suggestions')).toBe(true)

    // User manually modifies a row
    const rowInput = container.querySelector('input[aria-label="Translation for Bureau"]')
    expect(rowInput).toBeTruthy()
    expect(rowInput.value).toBe('Suggested Office')
    await act(async () => {
      setInputValue(rowInput, 'My Custom Office')
    })

    // Discard suggestions button MUST disappear
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent === 'Discard suggestions')).toBe(false)
    expect(rowInput.value).toBe('My Custom Office')
  })

  it('3.4 repair: legacy commit posts the preview once, then refreshes inventory', async () => {
    const legacyPreview = { version: 1, files: [] }
    const commitReport = { phase: 'commit', summary: { recorded: 1 }, files: [] }
    const calls = []
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        return []
      }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      calls.push({ url, body })
      if (url === '/api/resources/import/preview') {
        return { preview: legacyPreview, report: { phase: 'preview', summary: {}, files: [] } }
      }
      if (url === '/api/resources/import/commit') return { report: commitReport }
      return {}
    })

    await renderComponent()
    await switchToImportTab()
    const toggle = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Legacy Path Import (Compatibility)'))
    await act(async () => { toggle.click() })
    await act(async () => {
      setInputValue(container.querySelector('input[placeholder="e.g. /path/to/source_library.json"]'), '/fixture/source.json')
      setInputValue(container.querySelector('input[placeholder="e.g. rooms_main"]'), 'rooms_main')
    })
    const preview = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Legacy Import')
    await act(async () => { preview.click() })
    const getsBeforeCommit = libraryGets
    const commit = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Commit Legacy Import')
    await act(async () => { commit.click() })

    const commitCalls = calls.filter((call) => call.url === '/api/resources/import/commit')
    expect(commitCalls).toEqual([{ url: '/api/resources/import/commit', body: { preview: legacyPreview } }])
    expect(libraryGets).toBeGreaterThan(getsBeforeCommit)
    expect(container.textContent).toContain('Legacy Commit Result: 1 recorded.')
    expect(container.textContent).toContain('Legacy import committed successfully. Local resource inventory updated.')
  })

  it('3.4 repair: legacy commit success plus refresh failure is not retried or reported as a commit failure', async () => {
    const legacyPreview = { version: 1, files: [] }
    const postCalls = []
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets > 1) throw new Error('Inventory unavailable')
        return []
      }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      postCalls.push({ url, body })
      if (url === '/api/resources/import/preview') {
        return { preview: legacyPreview, report: { phase: 'preview', summary: {}, files: [] } }
      }
      if (url === '/api/resources/import/commit') {
        return { report: { phase: 'commit', summary: { recorded: 1 }, files: [] } }
      }
      return {}
    })

    await renderComponent()
    await switchToImportTab()
    const toggle = Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Legacy Path Import (Compatibility)'))
    await act(async () => { toggle.click() })
    await act(async () => {
      setInputValue(container.querySelector('input[placeholder="e.g. /path/to/source_library.json"]'), '/fixture/source.json')
      setInputValue(container.querySelector('input[placeholder="e.g. rooms_main"]'), 'rooms_main')
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Legacy Import').click()
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Commit Legacy Import').click()
    })

    expect(postCalls.filter((call) => call.url === '/api/resources/import/commit')).toHaveLength(1)
    expect(container.textContent).toContain('Legacy import committed, but failed to refresh inventory: Inventory unavailable')
    expect(container.textContent).not.toContain('ReferenceError')
  })

  it('3.4 repair: polling committed clears processing notice before refresh failure', async () => {
    const digest = '7'.repeat(64)
    const openView = makeOpenViewWithPreview('sel-poll-refresh-fail', 'poll_lib', 'room-1', digest)
    const committingView = { ...openView, selection_revision: 3, state: 'committing' }
    const committedView = makeCommittedView(openView, 'poll_lib', 'room-1', digest)
    let libraryGets = 0
    const postCalls = []
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets > 1) throw new Error('Refresh offline')
        return []
      }
      if (url.includes('/import-selections/sel-poll-refresh-fail')) return committedView
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      postCalls.push({ url, body })
      if (url === '/api/resources/import-selections') return openView
      if (url.endsWith('/commit')) return committingView
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)

    vi.useFakeTimers()
    try {
      await renderComponent()
      await switchToImportTab()
      await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
      await clickUpload()
      await act(async () => {
        Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import')).click()
      })
      expect(container.textContent).toContain('Commit is actively processing')

      await act(async () => { await vi.advanceTimersByTimeAsync(2500) })

      expect(container.textContent).not.toContain('Commit is actively processing')
      expect(container.textContent).toContain('Import committed, but failed to refresh inventory: Refresh offline')
      expect(postCalls.filter((call) => call.url.endsWith('/commit'))).toHaveLength(1)
      expect(postCalls.some((call) => call.url === '/api/sessions')).toBe(false)
      expect(postCalls.some((call) => call.url.includes('/translations/'))).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  const translationRaceFixture = () => {
    const pending = {
      id: 1,
      library_key: 'race_lib',
      display_name: 'Race Lib',
      kind: 'rooms',
      revisions: [{
        revision_id: 1,
        library_key: 'race_lib',
        source_id: 'room-1',
        content_digest: '9'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    const ready = {
      ...pending,
      revisions: [{ ...pending.revisions[0], readiness: { status: 'ready', pending_fields: {} } }],
    }
    const rows = {
      library_key: 'race_lib',
      kind: 'rooms',
      diagnostics: [],
      rows: [{
        revision: { source_id: 'room-1', content_digest: '9'.repeat(64) },
        field: 'label', source_field: 'name', source_value: 'Salon',
        source_shape: 'scalar', list_index: null, current_translation: null,
        translation: '', required: true, role: 'descriptive_input',
        identity_required: false, emit_by_default: true,
      }],
    }
    return { pending, ready, rows }
  }

  it('3.4 repair: edit during pending Apply cannot revive stale authorization and still refreshes to Ready', async () => {
    const { pending, ready, rows } = translationRaceFixture()
    let resolveApply
    const applyPromise = new Promise((resolve) => { resolveApply = resolve })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        return libraryGets === 1 ? [pending] : [ready]
      }
      if (url.endsWith('/translations/rows')) return rows
      return []
    })
    vi.spyOn(api, 'post').mockImplementation((url) => {
      if (url.endsWith('/translations/preview')) {
        return Promise.resolve({
          library_key: 'race_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0,
          would_be_ready: 1, would_remain_pending: 0,
          attestation_token: 'race-token', expires_at: 9999999999,
        })
      }
      if (url.endsWith('/translations/apply')) return applyPromise
      return Promise.resolve({})
    })

    await renderComponent()
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows').click()
    })
    const input = container.querySelector('input[aria-label="Translation for Salon"]')
    await act(async () => { setInputValue(input, 'Living Room') })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview manual translations').click()
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations').click()
    })
    expect(libraryGets).toBe(1)

    await act(async () => { setInputValue(input, 'Edited While Applying') })
    const staleApply = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    expect(staleApply.disabled).toBe(true)

    await act(async () => {
      resolveApply({ updated: 1, ready: 1, pending: 0 })
      await Promise.resolve()
    })

    expect(libraryGets).toBeGreaterThan(1)
    expect(container.textContent).toContain('Ready')
    expect(container.textContent).not.toContain('Manual preview ready')
    expect(container.textContent).not.toContain('Working…')
  })

  it('3.4 repair: Apply success plus refresh failure stays stale and reports refresh, not Apply, failure', async () => {
    const { pending, rows } = translationRaceFixture()
    let resolveApply
    const applyPromise = new Promise((resolve) => { resolveApply = resolve })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets > 1) throw new Error('Readiness unavailable')
        return [pending]
      }
      if (url.endsWith('/translations/rows')) return rows
      return []
    })
    vi.spyOn(api, 'post').mockImplementation((url) => {
      if (url.endsWith('/translations/preview')) {
        return Promise.resolve({
          library_key: 'race_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0,
          would_be_ready: 1, would_remain_pending: 0,
          attestation_token: 'race-token', expires_at: 9999999999,
        })
      }
      if (url.endsWith('/translations/apply')) return applyPromise
      return Promise.resolve({})
    })

    await renderComponent()
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Load editable rows').click()
    })
    const input = container.querySelector('input[aria-label="Translation for Salon"]')
    await act(async () => { setInputValue(input, 'Living Room') })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview manual translations').click()
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations').click()
    })
    await act(async () => { setInputValue(input, 'Edited While Applying') })
    await act(async () => {
      resolveApply({ updated: 1, ready: 1, pending: 0 })
      await Promise.resolve()
    })

    expect(libraryGets).toBeGreaterThan(1)
    expect(container.textContent).toContain('Translations applied, but failed to refresh readiness: Readiness unavailable')
    expect(container.textContent).toContain('Needs translation')
    expect(container.textContent).not.toContain('Apply failed')
    expect(container.textContent).not.toContain('ReadyCreate session')
    expect(container.textContent).not.toContain('Working…')
    const staleApply = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply manual translations')
    expect(staleApply.disabled).toBe(true)
  })

  it('3.4 final repair: user can start Preview B while Preview A is pending and late A stays stale', async () => {
    const library = {
      id: 1, library_key: 'map_busy_lib', display_name: 'Map Busy Lib', kind: 'rooms',
      revisions: [{
        revision_id: 1, library_key: 'map_busy_lib', source_id: 'room-1', content_digest: 'a'.repeat(64),
        readiness: { status: 'pending', pending_fields: { label: 'Missing' } },
      }],
      auxiliary: [],
    }
    let resolveA
    let resolveB
    const previewA = new Promise((resolve) => { resolveA = resolve })
    const previewB = new Promise((resolve) => { resolveB = resolve })
    vi.spyOn(api, 'get').mockImplementation(async (url) => (
      url === '/api/resources/libraries' ? [library] : []
    ))
    vi.spyOn(api, 'post').mockImplementation((url, body) => {
      if (url.endsWith('/translations/preview') && body.map_path === 'A.json') return previewA
      if (url.endsWith('/translations/preview') && body.map_path === 'B.json') return previewB
      return Promise.resolve({})
    })

    await renderComponent()
    const input = container.querySelector('input[placeholder*="translations.json"]')
    await act(async () => { typeInputValue(input, 'A.json') })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations').click()
    })
    expect(input.disabled).toBe(false)

    await act(async () => { typeInputValue(input, 'B.json') })
    expect(input.value).toBe('B.json')
    expect(input.disabled).toBe(false)
    const previewButton = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations')
    expect(previewButton.disabled).toBe(false)
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)

    await act(async () => { previewButton.click() })
    expect(input.disabled).toBe(false)

    await act(async () => {
      resolveA({
        library_key: 'map_busy_lib', total_revisions: 1, matched_revisions: 1,
        unmatched_map_entries: 0, would_update: 1, unchanged: 0,
        would_be_ready: 1, would_remain_pending: 0,
        attestation_token: 'token-a', expires_at: 9999999999,
      })
      await Promise.resolve()
    })
    expect(input.disabled).toBe(false)
    expect(input.value).toBe('B.json')
    expect(Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Working…').disabled).toBe(true)
    expect(container.textContent).not.toContain('Preview ready:')
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)

    await act(async () => {
      resolveB({
        library_key: 'map_busy_lib', total_revisions: 1, matched_revisions: 1,
        unmatched_map_entries: 0, would_update: 1, unchanged: 0,
        would_be_ready: 1, would_remain_pending: 0,
        attestation_token: 'token-b', expires_at: 9999999999,
      })
      await Promise.resolve()
    })
    expect(input.disabled).toBe(false)
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(true)
    expect(container.textContent).toContain('Preview ready: 1 of 1 revisions matched.')
  })

  it('3.4 final repair: map path stays editable during Apply while actions remain blocked through refresh', async () => {
    const { pending, ready } = translationRaceFixture()
    let resolveApply
    const applyPromise = new Promise((resolve) => { resolveApply = resolve })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        return libraryGets === 1 ? [pending] : [ready]
      }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation((url) => {
      if (url.endsWith('/translations/preview')) {
        return Promise.resolve({
          library_key: 'race_lib', total_revisions: 1, matched_revisions: 1,
          unmatched_map_entries: 0, would_update: 1, unchanged: 0,
          would_be_ready: 1, would_remain_pending: 0,
          attestation_token: 'map-token-a', expires_at: 9999999999,
        })
      }
      if (url.endsWith('/translations/apply')) return applyPromise
      return Promise.resolve({})
    })

    await renderComponent()
    const input = container.querySelector('input[placeholder*="translations.json"]')
    await act(async () => { typeInputValue(input, 'A.json') })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations').click()
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Confirm & Apply Translations').click()
    })

    expect(input.disabled).toBe(false)
    await act(async () => { typeInputValue(input, 'B.json') })
    expect(input.value).toBe('B.json')
    const previewButton = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations')
    expect(previewButton.disabled).toBe(true)
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)

    await act(async () => {
      resolveApply({ updated: 1, ready: 1, pending: 0 })
      await Promise.resolve()
    })

    expect(libraryGets).toBeGreaterThan(1)
    expect(container.textContent).toContain('Ready')
    expect(input.disabled).toBe(false)
    expect(input.value).toBe('B.json')
    expect(Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Preview Translations').disabled).toBe(false)
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.includes('Confirm & Apply Translations'))).toBe(false)
  })

  it('3.4 repair 2: commit waits for authoritative refresh after its own refresh is superseded', async () => {
    const digest = 'b'.repeat(64)
    const openView = makeOpenViewWithPreview('sel-superseded', 'refresh_lib', 'room-1', digest)
    const committedView = makeCommittedView(openView, 'refresh_lib', 'room-1', digest)
    const initial = [{ id: 1, library_key: 'initial_lib', display_name: 'Initial Inventory', kind: 'rooms', revisions: [], auxiliary: [] }]
    const oldData = [{ id: 2, library_key: 'old_lib', display_name: 'Stale Inventory A', kind: 'rooms', revisions: [], auxiliary: [] }]
    const newData = [{ id: 3, library_key: 'new_lib', display_name: 'Current Inventory B', kind: 'rooms', revisions: [], auxiliary: [] }]
    let resolveA
    let resolveB
    const refreshA = new Promise((resolve) => { resolveA = resolve })
    const refreshB = new Promise((resolve) => { resolveB = resolve })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation((url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets === 1) return Promise.resolve(initial)
        if (libraryGets === 2) return refreshA
        return refreshB
      }
      return Promise.resolve([])
    })
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
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import')).click()
    })
    const viewReadiness = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'View readiness in Inventory')
    await act(async () => { viewReadiness.click() })

    await act(async () => { resolveA(oldData); await Promise.resolve() })
    expect(container.textContent).not.toContain('Local resource inventory updated')
    expect(container.textContent).not.toContain('Stale Inventory A')

    await act(async () => { resolveB(newData); await Promise.resolve() })
    expect(container.textContent).toContain('Current Inventory B')
    expect(container.textContent).toContain('Local resource inventory updated')
  })

  it('3.4 repair 2: superseding current refresh failure reports committed but refresh failed', async () => {
    const digest = 'c'.repeat(64)
    const openView = makeOpenViewWithPreview('sel-superseded-fail', 'refresh_lib', 'room-1', digest)
    const committedView = makeCommittedView(openView, 'refresh_lib', 'room-1', digest)
    let resolveA
    let rejectB
    const refreshA = new Promise((resolve) => { resolveA = resolve })
    const refreshB = new Promise((resolve, reject) => { rejectB = reject })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation((url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets === 1) return Promise.resolve([])
        if (libraryGets === 2) return refreshA
        return refreshB
      }
      return Promise.resolve([])
    })
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
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import')).click()
    })
    await act(async () => {
      Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'View readiness in Inventory').click()
    })
    await act(async () => { resolveA([]); await Promise.resolve() })
    expect(container.textContent).not.toContain('Local resource inventory updated')

    await act(async () => { rejectB(new Error('Current refresh failed')); await Promise.resolve() })
    expect(container.textContent).toContain('Import committed, but failed to refresh inventory: Current refresh failed')
    expect(container.textContent).not.toContain('Local resource inventory updated')
  })

  it('3.4 repair 2: polling committed also waits for a superseding authoritative refresh', async () => {
    const digest = 'd'.repeat(64)
    const openView = makeOpenViewWithPreview('sel-poll-superseded', 'poll_refresh_lib', 'room-1', digest)
    const committingView = { ...openView, selection_revision: 3, state: 'committing' }
    const committedView = makeCommittedView(openView, 'poll_refresh_lib', 'room-1', digest)
    const currentData = [{ id: 4, library_key: 'poll_current', display_name: 'Polling Current Inventory', kind: 'rooms', revisions: [], auxiliary: [] }]
    let resolveA
    let resolveB
    const refreshA = new Promise((resolve) => { resolveA = resolve })
    const refreshB = new Promise((resolve) => { resolveB = resolve })
    let libraryGets = 0
    vi.spyOn(api, 'get').mockImplementation((url) => {
      if (url === '/api/resources/libraries') {
        libraryGets++
        if (libraryGets === 1) return Promise.resolve([])
        if (libraryGets === 2) return refreshA
        return refreshB
      }
      if (url.includes('/import-selections/sel-poll-superseded')) return Promise.resolve(committedView)
      return Promise.resolve([])
    })
    vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url === '/api/resources/import-selections') return openView
      if (url.endsWith('/commit')) return committingView
      return {}
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(openView)

    vi.useFakeTimers()
    try {
      await renderComponent()
      await switchToImportTab()
      await selectFiles([new File(['1'], 'f1.json', { type: 'application/json' })])
      await clickUpload()
      await act(async () => {
        Array.from(container.querySelectorAll('button')).find((b) => b.textContent.includes('Commit Import')).click()
      })
      expect(container.textContent).toContain('Commit is actively processing')

      await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
      await act(async () => {
        Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'View readiness in Inventory').click()
      })
      await act(async () => { resolveA([]); await Promise.resolve() })
      expect(container.textContent).not.toContain('Local resource inventory updated')

      await act(async () => { resolveB(currentData); await Promise.resolve() })
      expect(container.textContent).toContain('Polling Current Inventory')
      expect(container.textContent).toContain('Local resource inventory updated')
    } finally {
      vi.useRealTimers()
    }
  })
})
