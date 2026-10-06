import { reactive } from 'vue'
import { api, type DataStatus } from '@/api'
import { errorText } from '@/api/http'

/** Lake summary shared by the top bar and the data-management pages. */
export const dataStatus = reactive<{ value: DataStatus | null; error: string; loading: boolean }>({
  value: null,
  error: '',
  loading: false,
})

export async function refreshDataStatus(): Promise<void> {
  dataStatus.loading = true
  try {
    dataStatus.value = await api.status()
    dataStatus.error = ''
  } catch (e) {
    dataStatus.error = errorText(e)
  } finally {
    dataStatus.loading = false
  }
}
