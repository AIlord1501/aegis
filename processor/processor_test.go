package processor

import (
	"sync"
	"testing"
	"time"
)

func TestNormalizeRejectsMissingRequiredFields(t *testing.T) {
	if _, err := Normalize([]byte(`{"service_id":0,"severity":"error","source":"api","message":"failed"}`)); err == nil {
		t.Fatal("expected validation error")
	}
}

func TestNormalizeDefaultsAttributesAndFingerprint(t *testing.T) {
	event, err := Normalize([]byte(`{"service_id":7,"occurred_at":"2026-11-12T10:00:00Z","received_at":"2026-11-12T10:00:01Z","severity":"error","source":"api","message":"failed"}`))
	if err != nil {
		t.Fatal(err)
	}
	if len(event.Attributes) != 0 || event.Fingerprint == "" {
		t.Fatalf("normalization failed: %+v", event)
	}
	if event.OccurredAt.Location() != time.UTC {
		t.Fatal("timestamp was not normalized to UTC")
	}
	if Fingerprint(event) != event.Fingerprint {
		t.Fatal("fingerprint is not deterministic")
	}
}

func TestNormalizeIsSafeConcurrently(t *testing.T) {
	payload := []byte(`{"service_id":7,"occurred_at":"2026-11-12T10:00:00Z","severity":"error","source":"api","message":"failed"}`)
	var wait sync.WaitGroup
	for i := 0; i < 32; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			event, err := Normalize(payload)
			if err != nil || event.Fingerprint == "" {
				t.Errorf("concurrent normalization failed: %v", err)
			}
		}()
	}
	wait.Wait()
}

func TestConfigAppliesSafeDefaults(t *testing.T) {
	config := (Config{}).withDefaults()
	if config.Workers != 4 || config.MaxRetries != 3 || config.Stream == "" || config.Group == "" {
		t.Fatalf("unexpected defaults: %+v", config)
	}
}
