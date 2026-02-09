-- Migration 005: Clear vegetation_subscriptions to remove 'zombie' records created during 500 errors
-- This allows users to retry subscription creation cleanly.
DELETE FROM vegetation_subscriptions;
