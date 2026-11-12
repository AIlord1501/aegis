package processor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

const (
	DefaultStream        = "aegis:events"
	DefaultConsumerGroup = "aegis-processors"
	DefaultDeadLetter    = "aegis:events:dead-letter"
)

type Event struct {
	ServiceID   int            `json:"service_id"`
	EventID     *string        `json:"event_id"`
	OccurredAt  time.Time      `json:"occurred_at"`
	ReceivedAt  time.Time      `json:"received_at"`
	Severity    string         `json:"severity"`
	Source      string         `json:"source"`
	Message     string         `json:"message"`
	Attributes  map[string]any `json:"attributes"`
	Fingerprint string         `json:"fingerprint"`
}

type Config struct {
	Stream     string
	Group      string
	Consumer   string
	DeadLetter string
	Workers    int
	MaxRetries int
	ReadBlock  time.Duration
}

func (c Config) withDefaults() Config {
	if c.Stream == "" {
		c.Stream = DefaultStream
	}
	if c.Group == "" {
		c.Group = DefaultConsumerGroup
	}
	if c.DeadLetter == "" {
		c.DeadLetter = DefaultDeadLetter
	}
	if c.Workers < 1 {
		c.Workers = 4
	}
	if c.MaxRetries < 1 {
		c.MaxRetries = 3
	}
	if c.ReadBlock <= 0 {
		c.ReadBlock = time.Second
	}
	return c
}

type Processor struct {
	redis  redis.UniversalClient
	db     *pgxpool.Pool
	config Config
	logger *slog.Logger
}

func New(rdb redis.UniversalClient, db *pgxpool.Pool, config Config, logger *slog.Logger) *Processor {
	if logger == nil {
		logger = slog.Default()
	}
	return &Processor{redis: rdb, db: db, config: config.withDefaults(), logger: logger}
}

func Normalize(raw []byte) (Event, error) {
	var event Event
	if err := json.Unmarshal(raw, &event); err != nil {
		return Event{}, fmt.Errorf("decode event: %w", err)
	}
	if event.ServiceID <= 0 || event.OccurredAt.IsZero() || event.Severity == "" || event.Source == "" || event.Message == "" {
		return Event{}, errors.New("event is missing required fields")
	}
	if event.Attributes == nil {
		event.Attributes = map[string]any{}
	}
	event.OccurredAt = event.OccurredAt.UTC()
	if event.ReceivedAt.IsZero() {
		event.ReceivedAt = time.Now().UTC()
	} else {
		event.ReceivedAt = event.ReceivedAt.UTC()
	}
	if event.Fingerprint == "" {
		event.Fingerprint = Fingerprint(event)
	}
	return event, nil
}

func Fingerprint(event Event) string {
	canonical := struct {
		Attributes map[string]any `json:"attributes"`
		EventID    *string        `json:"event_id"`
		Message    string         `json:"message"`
		OccurredAt string         `json:"occurred_at"`
		ServiceID  int            `json:"service_id"`
		Severity   string         `json:"severity"`
		Source     string         `json:"source"`
	}{event.Attributes, event.EventID, event.Message, event.OccurredAt.UTC().Format(time.RFC3339Nano), event.ServiceID, event.Severity, event.Source}
	b, _ := json.Marshal(canonical)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (p *Processor) EnsureGroup(ctx context.Context) error {
	err := p.redis.XGroupCreateMkStream(ctx, p.config.Stream, p.config.Group, "0").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create consumer group: %w", err)
	}
	return nil
}

func (p *Processor) Run(ctx context.Context) error {
	if err := p.EnsureGroup(ctx); err != nil {
		return err
	}
	jobs := make(chan redis.XMessage)
	var workers sync.WaitGroup
	for i := 0; i < p.config.Workers; i++ {
		workers.Add(1)
		go func(workerID int) { defer workers.Done(); p.worker(ctx, workerID, jobs) }(i)
	}
	defer func() { close(jobs); workers.Wait() }()

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}
		streams, err := p.redis.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group: p.config.Group, Consumer: p.config.Consumer, Streams: []string{p.config.Stream, ">"}, Count: int64(p.config.Workers), Block: p.config.ReadBlock,
		}).Result()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			if errors.Is(err, redis.Nil) {
				continue
			}
			return fmt.Errorf("read event stream: %w", err)
		}
		for _, stream := range streams {
			for _, message := range stream.Messages {
				select {
				case jobs <- message:
				case <-ctx.Done():
					return nil
				}
			}
		}
	}
}

func (p *Processor) worker(ctx context.Context, workerID int, jobs <-chan redis.XMessage) {
	for message := range jobs {
		if err := p.process(ctx, message); err != nil {
			p.logger.Error("event permanently failed", "worker", workerID, "stream_id", message.ID, "error", err)
		} else {
			p.logger.Info("event processed", "worker", workerID, "stream_id", message.ID)
		}
	}
}

func (p *Processor) process(ctx context.Context, message redis.XMessage) error {
	payload, ok := message.Values["payload"].(string)
	if !ok {
		return p.deadLetter(ctx, message, errors.New("payload field missing"))
	}
	var lastErr error
	for attempt := 1; attempt <= p.config.MaxRetries; attempt++ {
		event, err := Normalize([]byte(payload))
		if err == nil {
			err = p.persist(ctx, event)
		}
		if err == nil {
			if ackErr := p.redis.XAck(ctx, p.config.Stream, p.config.Group, message.ID).Err(); ackErr != nil {
				return fmt.Errorf("ack event: %w", ackErr)
			}
			return nil
		}
		lastErr = err
		p.logger.Warn("event processing retry", "stream_id", message.ID, "attempt", attempt, "error", err)
	}
	return p.deadLetter(ctx, message, lastErr)
}

func (p *Processor) persist(ctx context.Context, event Event) error {
	attributes, err := json.Marshal(event.Attributes)
	if err != nil {
		return fmt.Errorf("encode attributes: %w", err)
	}
	_, err = p.db.Exec(ctx, `
		INSERT INTO events (service_id, event_id, occurred_at, received_at, severity, source, message, attributes, fingerprint)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
		ON CONFLICT (fingerprint) DO NOTHING`,
		event.ServiceID, event.EventID, event.OccurredAt, event.ReceivedAt, event.Severity, event.Source, event.Message, attributes, event.Fingerprint)
	if err != nil {
		return fmt.Errorf("persist event: %w", err)
	}
	return nil
}

func (p *Processor) deadLetter(ctx context.Context, message redis.XMessage, cause error) error {
	if err := p.redis.XAdd(ctx, &redis.XAddArgs{Stream: p.config.DeadLetter, Values: map[string]any{
		"original_stream": p.config.Stream, "original_id": message.ID, "payload": message.Values["payload"], "error": cause.Error(),
	}}).Err(); err != nil {
		return fmt.Errorf("dead-letter event: %w", err)
	}
	if err := p.redis.XAck(ctx, p.config.Stream, p.config.Group, message.ID).Err(); err != nil {
		return fmt.Errorf("ack dead-lettered event: %w", err)
	}
	return fmt.Errorf("event moved to dead-letter stream: %w", cause)
}
