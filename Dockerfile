# Dockerfile — NYX PHP Relay Server
#
# Build:  docker build -t nyx-server .
# Run:    docker run -p 8080:80 nyx-server

FROM php:8.1-apache

# Install PDO extensions for PostgreSQL and SQLite
RUN docker-php-ext-install pdo pdo_mysql pdo_pgsql pdo_sqlite

# Enable mod_rewrite for Apache
RUN a2enmod rewrite

# Set the document root to the server directory
ENV APACHE_DOCUMENT_ROOT=/var/www/html/server

# Update Apache configuration to use the new document root
RUN sed -ri -e 's!/var/www/html!${APACHE_DOCUMENT_ROOT}!g' /etc/apache2/sites-available/*.conf
RUN sed -ri -e 's!/var/www/!${APACHE_DOCUMENT_ROOT}!g' /etc/apache2/apache2.conf /etc/apache2/conf-available/*.conf

# Copy server files
COPY server/ /var/www/html/server/

# Set environment variables (override at runtime)
ENV DRIVER=sqlite
ENV DATABASE_URL=""

# Expose port 80
EXPOSE 80

# Start Apache
CMD ["apache2-foreground"]