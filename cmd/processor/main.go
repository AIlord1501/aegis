package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/aegis-platform/aegis/processor"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
func envInt(key string, fallback int) int {
	value, err := strconv.Atoi(env(key, ""))
	if err != nil {
		return fallback
	}
	return value
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	databaseURL := env("DATABASE_URL", "postgres://aegis:aegis@localhost:5432/aegis")
	db, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		logger.Error("database pool failed", "error", err)
		os.Exit(1)
	}
	defer db.Close()
	redisClient := redis.NewClient(&redis.Options{Addr: env("REDIS_ADDR", "localhost:6379")})
	defer redisClient.Close()

	config := processor.Config{Stream: env("EVENT_STREAM_KEY", processor.DefaultStream), Group: env("EVENT_CONSUMER_GROUP", processor.DefaultConsumerGroup), Consumer: env("EVENT_CONSUMER_NAME", env("HOSTNAME", "processor-1")), DeadLetter: env("EVENT_DEAD_LETTER_STREAM", processor.DefaultDeadLetter), Workers: envInt("EVENT_WORKERS", 4), MaxRetries: envInt("EVENT_MAX_RETRIES", 3)}
	p := processor.New(redisClient, db, config, logger)
	go serveHealth(ctx, redisClient, db, logger)
	logger.Info("event processor started", "stream", config.Stream, "group", config.Group, "workers", config.Workers)
	if err := p.Run(ctx); err != nil && ctx.Err() == nil {
		logger.Error("event processor stopped", "error", err)
		os.Exit(1)
	}
	logger.Info("event processor stopped")
}

func serveHealth(ctx context.Context, redisClient *redis.Client, db *pgxpool.Pool, logger *slog.Logger) {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, request *http.Request) {
		if err := redisClient.Ping(request.Context()).Err(); err != nil {
			http.Error(w, "redis unavailable", http.StatusServiceUnavailable)
			return
		}
		if err := db.Ping(request.Context()); err != nil {
			http.Error(w, "postgres unavailable", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ready"}`))
	})
	server := &http.Server{Addr: ":8081", Handler: loggingHandler(mux, logger), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("health server failed", "error", err)
		}
	}()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
}

func loggingHandler(next http.Handler, logger *slog.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		logger.Info("health request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
	})
}
