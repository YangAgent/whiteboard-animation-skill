#!/usr/bin/env python3

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_template import whiteboard_prompt_template

# --- Config ---
API_BASE = 'https://www.runninghub.cn/openapi/v2'
TEXT_TO_IMAGE_PATH = '/alibaba/wan-2.7/text-to-image'
QUERY_PATH = '/query'

MAX_RETRIES = 3
SUBMIT_MAX_RETRIES = 8
POLL_MAX_RETRIES = 5

RETRY_BASE_DELAY_S = 3.0
POLL_INTERVAL_S = 5.0

BATCH_CONCURRENCY = 10

SCRIPT_DIR = Path(__file__).resolve().parent


# --- Load .env from skill directory ---
def load_env():
    if os.environ.get('RUNNINGHUB_API_KEY'):
        return
    env_path = SCRIPT_DIR.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith('#'):
                continue
            eq_index = trimmed.find('=')
            if eq_index == -1:
                continue
            key = trimmed[:eq_index].strip()
            value = trimmed[eq_index + 1:].strip()
            if key == 'RUNNINGHUB_API_KEY' and value:
                os.environ['RUNNINGHUB_API_KEY'] = value
                return


# --- Error classification ---
class RetryableError(Exception):
    """Errors worth retrying (rate-limit, server error, network)."""
    def __init__(self, message, *, is_rate_limit=False):
        super().__init__(message)
        self.is_rate_limit = is_rate_limit


class FatalError(Exception):
    """Errors that should not be retried (bad request, auth, etc)."""
    pass


def classify_error(e):
    """Return (retryable, is_rate_limit) for a given exception."""
    msg = str(e).lower()
    if isinstance(e, FatalError):
        return False, False
    if 'http 429' in msg or 'rate' in msg or 'too many' in msg:
        return True, True
    if 'http 5' in msg:
        return True, False
    # Default: treat as retryable network/transient error
    return True, False


# --- HTTP helper (synchronous, used in thread) ---
def request_sync(method, url_path, body):
    api_key = os.environ.get('RUNNINGHUB_API_KEY')
    if not api_key:
        raise FatalError('RUNNINGHUB_API_KEY not found. Set it in environment variable or .env file.')

    url = API_BASE + url_path
    payload = json.dumps(body).encode('utf-8')
    req = Request(url, data=payload, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data)
    except HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')
        if e.code == 400 or e.code == 401 or e.code == 403:
            raise FatalError(f'HTTP {e.code}: {body_text}')
        if e.code == 429:
            raise RetryableError(f'HTTP 429 (rate limited): {body_text}', is_rate_limit=True)
        # 5xx and other codes are retryable
        raise RetryableError(f'HTTP {e.code}: {body_text}')
    except json.JSONDecodeError as e:
        raise RetryableError(f'Failed to parse response: {e}')
    except Exception as e:
        raise RetryableError(str(e))


# --- Retry wrapper with exponential backoff + jitter ---
def calc_backoff(attempt, base=RETRY_BASE_DELAY_S, is_rate_limit=False):
    """Exponential backoff with jitter. Rate-limit errors get 2x longer wait."""
    multiplier = 2.0 if is_rate_limit else 1.0
    delay = base * (2 ** (attempt - 1)) * multiplier
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter


async def with_retry(fn, max_retries=MAX_RETRIES, context=''):
    for attempt in range(1, max_retries + 1):
        try:
            return await fn()
        except FatalError:
            raise
        except RetryableError as e:
            if attempt == max_retries:
                raise
            delay = calc_backoff(attempt, is_rate_limit=e.is_rate_limit)
            print(f'{context}Attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s...')
            await asyncio.sleep(delay)
        except Exception as e:
            retryable, is_rate_limit = classify_error(e)
            if not retryable or attempt == max_retries:
                raise
            delay = calc_backoff(attempt, is_rate_limit=is_rate_limit)
            print(f'{context}Attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s...')
            await asyncio.sleep(delay)


# --- Step 1: Submit text-to-image task ---
def image_size_for_aspect_ratio(aspect_ratio):
    normalized = aspect_ratio.strip()
    if normalized == '9:16':
        return 1440, 2560
    return 2560, 1440


def _submit_task_sync(prompt, aspect_ratio):
    width, height = image_size_for_aspect_ratio(aspect_ratio)
    res = request_sync('POST', TEXT_TO_IMAGE_PATH, {
        'prompt': prompt,
        'width': width,
        'height': height,
        'thinkingMode': True,
    })
    if not res.get('taskId'):
        raise RetryableError(f'No taskId in response: {json.dumps(res)}')
    return res['taskId']


async def submit_task(prompt, aspect_ratio, context=''):
    async def _do():
        task_id = await asyncio.to_thread(_submit_task_sync, prompt, aspect_ratio)
        print(f'{context}Task submitted: {task_id}')
        return task_id
    return await with_retry(_do, max_retries=SUBMIT_MAX_RETRIES, context=context)


# --- Step 2: Poll for result ---
async def poll_result(task_id, context=''):
    poll_errors = 0

    while True:
        try:
            res = await asyncio.to_thread(
                request_sync, 'POST', QUERY_PATH, {'taskId': task_id}
            )
            # Only consecutive poll errors count toward the retry limit.
            poll_errors = 0
            status = res.get('status')

            if status == 'SUCCESS':
                results = res.get('results')
                if not results or len(results) == 0 or not results[0].get('url'):
                    raise RetryableError(f'SUCCESS but no image URL: {json.dumps(res)}')
                return results[0]

            if status == 'FAILED':
                # Signal caller to re-submit; retry count is managed by generate_single
                return {'_failed': True, 'res': res}

            # QUEUED or RUNNING
            print(f'{context}Status: {status}. Polling in {POLL_INTERVAL_S}s...')
            await asyncio.sleep(POLL_INTERVAL_S)

        except FatalError:
            raise
        except RetryableError as e:
            poll_errors += 1
            if poll_errors > POLL_MAX_RETRIES:
                raise
            delay = calc_backoff(poll_errors, is_rate_limit=e.is_rate_limit)
            print(f'{context}Poll error (retry {poll_errors}/{POLL_MAX_RETRIES}): {e}. Waiting {delay:.1f}s...')
            await asyncio.sleep(delay)


# --- Step 3: Download image ---
def download_file(url, dest_path):
    """Download file with redirect support"""
    import shutil
    from urllib.request import urlopen

    with urlopen(url) as resp:
        if resp.status >= 300 and resp.status < 400:
            location = resp.headers.get('Location')
            if location:
                return download_file(location, dest_path)
        if resp.status != 200:
            raise RuntimeError(f'Download failed with status {resp.status}')
        with open(dest_path, 'wb') as f:
            shutil.copyfileobj(resp, f)
    return dest_path


# --- Generate single image ---
async def generate_single(prompt, aspect_ratio, output_dir, index, total):
    tag = f'[{index + 1}/{total}] ' if total > 1 else ''

    submit_retries = 0
    while submit_retries <= POLL_MAX_RETRIES:
        task_id = await submit_task(prompt, aspect_ratio, context=tag)
        result = await poll_result(task_id, context=tag)

        if result.get('_failed'):
            submit_retries += 1
            if submit_retries > POLL_MAX_RETRIES:
                raise RetryableError(f'{tag}Max retries exceeded for task submission.')
            delay = calc_backoff(submit_retries)
            print(f'{tag}Re-submitting task (attempt {submit_retries}/{POLL_MAX_RETRIES}) in {delay:.1f}s...')
            await asyncio.sleep(delay)
            continue
        break

    # Download with retry
    ext = result.get('outputType', 'png')
    timestamp = int(time.time() * 1000)
    suffix = f'_{str(index + 1).zfill(len(str(total)))}' if total > 1 else ''
    filename = f'img_{timestamp}{suffix}.{ext}'
    filepath = str(Path(output_dir) / filename)

    async def _download():
        print(f'{tag}Downloading image to {filepath}...')
        await asyncio.to_thread(download_file, result['url'], filepath)
        print(f'{tag}Image saved: {filepath}')
        return filepath

    return await with_retry(_download, max_retries=MAX_RETRIES, context=tag)


# --- Batch runner with concurrency control + final retry for failures ---
async def run_batch(tasks, concurrency):
    semaphore = asyncio.Semaphore(concurrency)
    results = [None] * len(tasks)

    async def worker(i, task):
        async with semaphore:
            try:
                results[i] = await generate_single(
                    task['prompt'], task['aspectRatio'],
                    task['outputDir'], task['index'], task['total']
                )
            except Exception as e:
                results[i] = {'error': str(e), 'task': task}

    await asyncio.gather(*(worker(i, t) for i, t in enumerate(tasks)))

    # Final retry pass: retry all failed tasks (with same concurrency limit)
    failed_indices = [i for i, r in enumerate(results) if isinstance(r, dict) and r.get('error')]
    if failed_indices:
        print(f'\nRetrying {len(failed_indices)} failed tasks...')
        await asyncio.sleep(RETRY_BASE_DELAY_S)

        async def retry_worker(i):
            async with semaphore:
                task = results[i]['task']
                try:
                    results[i] = await generate_single(
                        task['prompt'], task['aspectRatio'],
                        task['outputDir'], task['index'], task['total']
                    )
                except Exception as e:
                    results[i] = {'error': str(e)}

        await asyncio.gather(*(retry_worker(i) for i in failed_indices))

    return results


# --- Main ---
async def main():
    load_env()
    args = sys.argv[1:]
    prompt_arg = args[0] if len(args) > 0 else ''
    aspect_ratio = args[1] if len(args) > 1 else '16:9'
    output_dir = args[2] if len(args) > 2 else os.getcwd()

    if not prompt_arg.strip():
        print('Error: prompt is required and cannot be empty.')
        sys.exit(1)

    # Ensure output directory exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Detect batch mode: prompt is a JSON-encoded array of strings
    prompts = None
    try:
        parsed = json.loads(prompt_arg)
        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], str):
            prompts = parsed
    except (json.JSONDecodeError, ValueError):
        pass
    if not prompts:
        prompts = [prompt_arg]

    total = len(prompts)
    is_batch = total > 1

    if is_batch:
        print(f'Batch mode: generating {total} images (concurrency: {BATCH_CONCURRENCY})...')

    tasks = [
        {
            'prompt': whiteboard_prompt_template + prompt,
            'aspectRatio': aspect_ratio,
            'outputDir': output_dir,
            'index': i,
            'total': total,
        }
        for i, prompt in enumerate(prompts)
    ]

    results = await run_batch(tasks, BATCH_CONCURRENCY)

    # Summary
    succeeded = [r for r in results if isinstance(r, str)]
    failed = [r for r in results if isinstance(r, dict) and r.get('error')]
    if is_batch:
        print(f'\nBatch complete: {len(succeeded)} succeeded, {len(failed)} failed.')
    if failed:
        for f in failed:
            print(f"  Error: {f['error']}")

    # Output results as JSON for programmatic use
    print(f'\n__RESULTS__{json.dumps(results)}')


if __name__ == '__main__':
    asyncio.run(main())
