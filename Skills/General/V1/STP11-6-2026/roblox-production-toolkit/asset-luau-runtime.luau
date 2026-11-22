--!strict
-- Shared runtime helpers copied beside generated Luau modules by tools-code.py.
-- Pure helpers only: no DataStore, MessagingService, HttpService or Studio API is called here.
-- Copy this ModuleScript to ReplicatedStorage/Shared (client-safe) or ServerScriptService/Services (server-only).

local Runtime = {}

export type Bucket = {
	capacity: number,
	refillPerSecond: number,
	tokens: number,
	lastTime: number,
}

export type Lease = {
	owner: string,
	expires: number,
	token: number,
}

-- Token bucket: refills continuously and never exceeds capacity.
function Runtime.newBucket(capacity: number, refillPerSecond: number, now: number): Bucket
	assert(capacity > 0 and refillPerSecond > 0, "capacity and refillPerSecond must be positive")
	return { capacity = capacity, refillPerSecond = refillPerSecond, tokens = capacity, lastTime = now }
end

function Runtime.take(bucket: Bucket, now: number, cost: number?): boolean
	local amount = cost or 1
	local elapsed = math.max(0, now - bucket.lastTime)
	bucket.tokens = math.min(bucket.capacity, bucket.tokens + elapsed * bucket.refillPerSecond)
	bucket.lastTime = now
	if bucket.tokens >= amount then
		bucket.tokens -= amount
		return true
	end
	return false
end

-- WaitForChild with a clamped timeout so a missing dependency cannot block forever.
function Runtime.waitForChildBounded(parent: Instance, name: string, timeout: number): Instance?
	return parent:WaitForChild(name, math.clamp(timeout, 0.1, 30))
end

-- Bounded retry with exponential delay. fn returns (success, value).
function Runtime.retry(attempts: number, baseDelay: number, maxDelay: number, fn: () -> (boolean, any)): (boolean, any)
	local limit = math.clamp(attempts, 1, 10)
	local lastValue: any = nil
	for attempt = 1, limit do
		local ok, value = fn()
		if ok then
			return true, value
		end
		lastValue = value
		if attempt < limit then
			task.wait(math.min(maxDelay, baseDelay * 2 ^ (attempt - 1)))
		end
	end
	return false, lastValue
end

-- Fenced lease decision. Returns the new lease, or nil while another owner's lease is live.
-- The monotonically increasing token must be stored with every write so stale owners can be fenced.
function Runtime.acquireLease(current: Lease?, owner: string, now: number, leaseSeconds: number): Lease?
	if current ~= nil and current.owner ~= owner and current.expires > now then
		return nil
	end
	local token = if current then current.token + 1 else 1
	return { owner = owner, expires = now + leaseSeconds, token = token }
end

-- Accept a write only when its fencing token is the newest one seen.
function Runtime.isFencedWriteAllowed(stored: Lease?, writerToken: number): boolean
	if stored == nil then
		return true
	end
	return writerToken >= stored.token
end

return Runtime
