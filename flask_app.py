"""
FPL League Analyzer - Flask Application
Modern web application for analyzing Fantasy Premier League leagues
"""

import logging
import os
import secrets

from flask import Flask, jsonify, render_template, request

import config
from extensions import cache, cors
from routes import main_bp, api_bp

logger = logging.getLogger(__name__)


def configure_logging(level_name: str) -> None:
    """Logging tập trung thay cho print() rải rác trong code."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    # requests/urllib3 rất ồn ở DEBUG
    logging.getLogger('urllib3').setLevel(max(level, logging.WARNING))


def _resolve_secret_key() -> str:
    """SECRET_KEY: bắt buộc phải cấu hình khi chạy production.

    Trước đây có giá trị mặc định hardcode - nghĩa là mọi deployment không set
    biến môi trường đều dùng chung một khoá ai cũng đọc được trong source.
    """
    if config.SECRET_KEY:
        return config.SECRET_KEY

    # App hiện không dùng session nên khoá ngẫu nhiên mỗi lần khởi động là an
    # toàn - vẫn tốt hơn nhiều so với một khoá hardcode nằm trong source.
    log = logger.error if not config.DEBUG else logger.warning
    log("SECRET_KEY chưa cấu hình - dùng khoá ngẫu nhiên tạm cho tiến trình này. "
        "Hãy đặt SECRET_KEY trong .env trước khi deploy.")
    return secrets.token_hex(32)


def create_app(config_object=None):
    """
    Application factory pattern

    Args:
        config_object: Optional mapping/object để override app.config (dùng cho test)

    Returns:
        Flask application instance
    """
    configure_logging(config.LOG_LEVEL)

    app = Flask(__name__)

    app.config['SECRET_KEY'] = _resolve_secret_key()
    app.config['JSON_SORT_KEYS'] = False

    # Cache configuration - disk-based for persistence across restarts
    app.config['CACHE_TYPE'] = config.CACHE_TYPE
    app.config['CACHE_DIR'] = config.CACHE_DIR
    app.config['CACHE_DEFAULT_TIMEOUT'] = config.CACHE_DEFAULT_TIMEOUT
    app.config['CACHE_THRESHOLD'] = config.CACHE_THRESHOLD

    if config_object:
        app.config.from_object(config_object)

    # Create cache directory if it doesn't exist (đường dẫn tuyệt đối, không phụ thuộc CWD)
    os.makedirs(app.config['CACHE_DIR'], exist_ok=True)

    # Initialize extensions
    cache.init_app(app)

    # Giao diện được phục vụ bởi chính app này nên mặc định không cần CORS.
    # Chỉ bật khi CORS_ORIGINS được khai báo tường minh.
    if config.CORS_ORIGINS and config.CORS_ORIGINS != ['']:
        cors.init_app(app, resources={r"/api/*": {"origins": config.CORS_ORIGINS}})
        logger.info("CORS enabled for /api/* origins=%s", config.CORS_ORIGINS)

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    register_error_handlers(app)

    # Add template globals
    @app.context_processor
    def inject_config():
        return {'config': config}

    logger.info(
        "App ready | debug=%s cache_dir=%s league=%s",
        config.DEBUG, app.config['CACHE_DIR'], config.DEFAULT_LEAGUE_ID,
    )
    return app


def register_error_handlers(app: Flask) -> None:
    """Trả JSON cho /api/*, trả HTML cho các trang thường.

    Trước đây mọi lỗi đều trả JSON nên người dùng gõ sai URL sẽ thấy
    ``{"error": "Not found"}`` thay vì một trang lỗi.
    """
    def wants_json() -> bool:
        return (
            request.path.startswith('/api/')
            or request.accept_mimetypes.best == 'application/json'
        )

    @app.errorhandler(404)
    def not_found(error):
        if wants_json():
            return jsonify({'success': False, 'error': 'Not found'}), 404
        return render_template('errors/404.html', page_title='Không tìm thấy'), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception("Internal server error on %s", request.path)
        if wants_json():
            return jsonify({'success': False, 'error': 'Internal server error'}), 500
        return render_template('errors/500.html', page_title='Lỗi máy chủ'), 500


if __name__ == '__main__':
    # Production dùng dạng factory: gunicorn "flask_app:create_app()"
    create_app().run(debug=config.DEBUG, host=config.HOST, port=config.PORT)
