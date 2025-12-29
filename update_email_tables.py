from datetime import datetime
from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, DateTime, Boolean

# Initialize SQLAlchemy components
Base = declarative_base()
metadata = MetaData()

class SendGridTemplate(Base):
    __tablename__ = 'sendgrid_template'
    
    id = Column(Integer, primary_key=True)
    sendgrid_id = Column(String(100), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    subject = Column(String(200))
    version = Column(String(50))
    active_version_id = Column(String(100))
    preview_url = Column(String(500))
    is_active = Column(Boolean, default=True, nullable=False)
    last_modified = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

def main():
    # Connect to the database in Downloads folder
    engine = create_engine('sqlite:////Users/christophernichols/Downloads/crm (4).db')
    
    try:
        # Create all tables
        Base.metadata.create_all(engine)
        print("Created sendgrid_template table successfully!")
        
        # Create a session
        Session = sessionmaker(bind=engine)
        session = Session()
        
        try:
            # Add the is_active column if it doesn't exist
            session.execute(text('ALTER TABLE sendgrid_template ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL'))
            session.commit()
            print("Added is_active column successfully!")
        except Exception as e:
            print(f"Note: {str(e)}")  # Column might already exist
            session.rollback()
        
        # Ensure all existing records have is_active set
        session.execute(text('UPDATE sendgrid_template SET is_active = 1 WHERE is_active IS NULL'))
        session.commit()
        print("Updated existing records successfully!")
        
        session.close()
        print("Database tables updated successfully!")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise

if __name__ == "__main__":
    main() 